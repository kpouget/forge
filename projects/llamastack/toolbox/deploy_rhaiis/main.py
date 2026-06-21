"""
Deploy RHAIIS/vLLM inference service for Llama Stack testing.

Migrated from tekton-benchmarks/tasks/deploy-rhaiis.yaml.
Handles:
- Creating PVC for model weights (if needed)
- Deploying ServingRuntime and InferenceService via KServe
- Waiting for the inference service to be ready
"""

from __future__ import annotations

import logging

from projects.core.dsl import always, entrypoint, execute_tasks, retry, task
from projects.core.dsl.utils.k8s import oc, oc_get_json, oc_resource_exists
from projects.core.library import env
from projects.llamastack.orchestration.runtime_config import cfg

logger = logging.getLogger(__name__)


@entrypoint
def run(
    *,
    namespace: str,
    model_name: str,
    pvc_name: str | None = None,
    pvc_size: str | None = None,
    deploy_timeout: int = 900,
) -> int:
    """
    Deploy RHAIIS inference service.

    Args:
        namespace: Target namespace
        model_name: InferenceService name
        pvc_name: PVC name for model weights (None = uses modelcar)
        pvc_size: PVC size (e.g. "120Gi")
        deploy_timeout: Timeout in seconds for deployment
    """
    execute_tasks(locals())
    return 0


@task
def ensure_pvc(args, ctx):
    """Create PVC for model weights if specified."""
    if not args.pvc_name:
        return "No PVC needed (using modelcar)"

    if oc_resource_exists("pvc", args.pvc_name, namespace=args.namespace):
        return f"PVC {args.pvc_name} already exists"

    import os
    import tempfile

    pvc_manifest = f"""apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {args.pvc_name}
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: {args.pvc_size}
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(pvc_manifest)
        manifest_path = f.name

    try:
        oc("apply", "-f", manifest_path, "-n", args.namespace, check=True)
    finally:
        os.unlink(manifest_path)

    return f"PVC {args.pvc_name} ({args.pvc_size}) created"


@task
def deploy_inference(args, ctx):
    """Deploy ServingRuntime and InferenceService."""
    manifests_dir = cfg.get_manifests_dir()

    # Grant SCC for AI inference workloads
    oc(
        "adm",
        "policy",
        "add-scc-to-user",
        "openshift-ai-llminferenceservice-scc",
        "-z",
        "default",
        "-n",
        args.namespace,
        check=False,
    )

    # Apply ServingRuntime
    runtime_manifest = manifests_dir / "rhaiis" / "servingruntime.yaml"
    if runtime_manifest.exists():
        oc("apply", "-f", str(runtime_manifest), "-n", args.namespace, check=True)

    # Apply InferenceService
    isvc_manifest = manifests_dir / "rhaiis" / "inferenceservice.yaml"
    if isvc_manifest.exists():
        oc("apply", "-f", str(isvc_manifest), "-n", args.namespace, check=True)

    return f"Inference service {args.model_name} deployed"


@retry(attempts=90, delay=10, backoff=1.0)
@task
def wait_for_ready(args, ctx):
    """Wait for InferenceService to be ready."""
    payload = oc_get_json(
        "inferenceservice",
        name=args.model_name,
        namespace=args.namespace,
        ignore_not_found=True,
    )
    if not payload:
        return (False, f"Waiting for InferenceService {args.model_name}")

    conditions = payload.get("status", {}).get("conditions", [])
    for cond in conditions:
        if cond.get("type") == "Ready" and cond.get("status") == "True":
            return f"InferenceService {args.model_name} is ready"

    return (False, f"InferenceService {args.model_name} not yet ready")


@always
@task
def capture_state(args, ctx):
    """Capture inference service state for diagnostics."""
    artifacts_dir = env.ARTIFACT_DIR / "artifacts" / "inference"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = oc(
        "get", "inferenceservice", "-n", args.namespace, "-o", "yaml", check=False, log_stdout=False
    )
    if result.returncode == 0 and result.stdout:
        (artifacts_dir / "inferenceservices.yaml").write_text(result.stdout, encoding="utf-8")

    result = oc(
        "get",
        "pods",
        "-n",
        args.namespace,
        "-l",
        f"serving.kserve.io/inferenceservice={args.model_name}",
        "-o",
        "wide",
        check=False,
        log_stdout=False,
    )
    if result.returncode == 0 and result.stdout:
        (artifacts_dir / "pods.txt").write_text(result.stdout, encoding="utf-8")

    return "State captured"
