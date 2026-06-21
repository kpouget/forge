"""
Deploy LlamaStackDistribution using the RHOAI operator-managed rh-dev distribution.

Migrated from tekton-benchmarks/tasks/deploy-llamastack.yaml.
Handles:
- Verifying/enabling the LlamaStack operator in the DataScienceCluster
- Deploying the LlamaStackDistribution CR
- Patching VLLM_URL for model name and namespace
- Optionally disabling OpenTelemetry
- Setting replica count and HPA configuration
- Waiting for the distribution to reach Ready state
- Pinning LlamaStack pods to GPU worker node
"""

from __future__ import annotations

import logging
from typing import Any

from projects.core.dsl import always, entrypoint, execute_tasks, retry, task
from projects.core.dsl.utils.k8s import oc, oc_get_json, oc_resource_exists
from projects.core.library import env
from projects.llamastack.orchestration.runtime_config import cfg

logger = logging.getLogger(__name__)


@entrypoint
def run(
    *,
    namespace: str,
    distribution_name: str,
    model_name: str,
    replicas: int = 1,
    disable_otel: bool = False,
    enable_hpa: bool = False,
    hpa_config: dict[str, Any] | None = None,
) -> int:
    """
    Deploy LlamaStackDistribution CR.

    Args:
        namespace: Target namespace
        distribution_name: Name of the LlamaStackDistribution CR
        model_name: vLLM model/InferenceService name
        replicas: Number of LlamaStack replicas
        disable_otel: If True, disables OpenTelemetry
        enable_hpa: If True, creates HPA for LlamaStack
        hpa_config: HPA configuration (min_replicas, max_replicas, memory_target)
    """
    execute_tasks(locals())
    return 0


def scale(*, namespace: str, distribution_name: str, replicas: int) -> None:
    """Scale an existing LlamaStack distribution to the given replica count."""
    logger.info("Scaling %s to %d replicas", distribution_name, replicas)
    oc(
        "patch",
        "llamastackdistribution",
        distribution_name,
        "-n",
        namespace,
        "--type",
        "merge",
        "-p",
        f'{{"spec":{{"replicas":{replicas}}}}}',
        check=True,
    )
    oc(
        "rollout",
        "status",
        "deployment",
        "-n",
        namespace,
        "-l",
        "app=llama-stack",
        "--timeout=180s",
        check=False,
    )


@task
def ensure_operator(args, ctx):
    """Verify the LlamaStack operator is running in RHOAI."""
    result = oc(
        "get",
        "deployment",
        "-n",
        "redhat-ods-applications",
        "-l",
        "app.kubernetes.io/name=llama-stack-k8s-operator",
        "-o",
        "name",
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        logger.warning("LlamaStack operator not found — enabling in DSC")
        oc(
            "patch",
            "datasciencecluster",
            "default-dsc",
            "--type",
            "merge",
            "-p",
            '{"spec":{"components":{"llamastackoperator":{"managementState":"Managed"}}}}',
            check=False,
        )
        import time

        time.sleep(30)
    return "LlamaStack operator verified"


@task
def cleanup_existing(args, ctx):
    """Delete existing LlamaStack distribution if present."""
    if oc_resource_exists(
        "llamastackdistribution", args.distribution_name, namespace=args.namespace
    ):
        logger.info("Cleaning up existing LlamaStack: %s", args.distribution_name)
        oc(
            "delete",
            "llamastackdistribution",
            args.distribution_name,
            "-n",
            args.namespace,
            "--ignore-not-found=true",
            check=False,
        )
        import time

        time.sleep(10)
    return "Cleanup complete"


@task
def deploy_distribution(args, ctx):
    """Deploy the LlamaStackDistribution CR."""
    manifests_dir = cfg.get_manifests_dir()
    manifest_path = manifests_dir / "llamastack.yaml"

    import yaml

    with open(manifest_path) as f:
        manifest = yaml.safe_load(f)

    manifest["metadata"]["name"] = args.distribution_name

    # Patch VLLM_URL to point to the correct predictor service
    predictor_url = f"http://{args.model_name}-predictor.{args.namespace}.svc.cluster.local:8080/v1"
    env_vars = manifest["spec"]["server"]["containerSpec"]["env"]
    for env_var in env_vars:
        if env_var["name"] == "VLLM_URL":
            env_var["value"] = predictor_url
        elif env_var["name"] == "INFERENCE_MODEL":
            env_var["value"] = args.model_name
        elif env_var["name"] == "OTEL_RESOURCE_ATTRIBUTES":
            env_var["value"] = f"k8s.namespace.name={args.namespace}"

    # Disable OTel if requested
    if args.disable_otel:
        otel_vars = {
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "OTEL_EXPORTER_OTLP_PROTOCOL",
            "OTEL_SERVICE_NAME",
            "OTEL_RESOURCE_ATTRIBUTES",
        }
        env_vars = [e for e in env_vars if e["name"] not in otel_vars]
        env_vars.append({"name": "OTEL_SDK_DISABLED", "value": "true"})
        manifest["spec"]["server"]["containerSpec"]["env"] = env_vars

    # Set replicas
    manifest["spec"]["replicas"] = args.replicas

    # Update network policy namespace
    if "network" in manifest["spec"]:
        namespaces = manifest["spec"]["network"].get("allowedFrom", {}).get("namespaces", [])
        manifest["spec"]["network"]["allowedFrom"]["namespaces"] = [
            args.namespace if ns == "NAMESPACE_PLACEHOLDER" else ns for ns in namespaces
        ]

    # Write and apply
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tmp:
        yaml.safe_dump(manifest, tmp, sort_keys=False)
        tmp_path = tmp.name

    oc("apply", "-f", tmp_path, "-n", args.namespace, check=True)

    import os

    os.unlink(tmp_path)

    # Save applied manifest to artifacts
    artifacts_src = env.ARTIFACT_DIR / "src"
    artifacts_src.mkdir(parents=True, exist_ok=True)
    with open(artifacts_src / "llamastack-applied.yaml", "w") as f:
        yaml.safe_dump(manifest, f, sort_keys=False)

    return f"LlamaStackDistribution {args.distribution_name} applied"


@retry(attempts=60, delay=5, backoff=1.0)
@task
def wait_for_ready(args, ctx):
    """Wait for LlamaStack distribution to reach Ready state."""
    payload = oc_get_json(
        "llamastackdistribution",
        name=args.distribution_name,
        namespace=args.namespace,
        ignore_not_found=True,
    )
    if not payload:
        return (False, "Waiting for LlamaStackDistribution to appear")

    phase = payload.get("status", {}).get("phase", "Unknown")
    if phase == "Ready":
        version_info = payload.get("status", {}).get("version", {})
        server_ver = version_info.get("llamaStackServerVersion", "unknown")
        operator_ver = version_info.get("operatorVersion", "unknown")
        logger.info("LlamaStack ready — server=%s, operator=%s", server_ver, operator_ver)
        ctx.server_version = server_ver
        ctx.operator_version = operator_ver
        return f"LlamaStack ready (server={server_ver}, operator={operator_ver})"

    return (False, f"Status: {phase}")


@task
def pin_to_gpu_node(args, ctx):
    """Pin LlamaStack pods to the GPU worker node for co-location with vLLM."""
    import json

    deploy_name = args.distribution_name
    result = oc(
        "get",
        "deployment",
        deploy_name,
        "-n",
        args.namespace,
        "--ignore-not-found",
        "-o",
        "name",
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return f"No LlamaStack deployment '{deploy_name}' found to pin"

    gpu_result = oc(
        "get",
        "nodes",
        "-l",
        "nvidia.com/gpu.present=true",
        "-o",
        "jsonpath={.items[0].metadata.name}",
        check=False,
    )
    if gpu_result.returncode != 0 or not gpu_result.stdout.strip():
        return "No GPU node found — skipping pin"

    gpu_node = gpu_result.stdout.strip()
    logger.info("Pinning %s to GPU node: %s", deploy_name, gpu_node)

    patch = {
        "spec": {
            "template": {
                "spec": {
                    "nodeSelector": {"kubernetes.io/hostname": gpu_node},
                    "tolerations": [
                        {"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"}
                    ],
                }
            }
        }
    }
    oc(
        "patch",
        "deployment",
        deploy_name,
        "-n",
        args.namespace,
        "--type",
        "merge",
        "-p",
        json.dumps(patch),
        check=False,
    )
    oc(
        "rollout",
        "status",
        f"deployment/{deploy_name}",
        "-n",
        args.namespace,
        "--timeout=120s",
        check=False,
    )

    return f"Pinned {deploy_name} to {gpu_node}"


@task
def configure_hpa(args, ctx):
    """Create HPA for LlamaStack if enabled."""
    if not args.enable_hpa:
        return "HPA not enabled — skipping"

    hpa_cfg = args.hpa_config or {}
    min_replicas = hpa_cfg.get("min_replicas", 1)
    max_replicas = hpa_cfg.get("max_replicas", 4)
    memory_target = hpa_cfg.get("memory_target", 75)

    logger.info(
        "Enabling HPA: min=%d, max=%d, memory=%d%%", min_replicas, max_replicas, memory_target
    )

    oc(
        "patch",
        "llamastackdistribution",
        args.distribution_name,
        "-n",
        args.namespace,
        "--type",
        "merge",
        "-p",
        (
            f'{{"spec":{{"server":{{"autoscaling":'
            f'{{"minReplicas":{min_replicas},"maxReplicas":{max_replicas},'
            f'"targetMemoryUtilizationPercentage":{memory_target}}}}}}}}}'
        ),
        check=True,
    )

    import time

    time.sleep(15)
    return f"HPA configured (min={min_replicas}, max={max_replicas}, mem={memory_target}%)"


@always
@task
def capture_state(args, ctx):
    """Capture LlamaStack state for diagnostics."""
    artifacts_dir = env.ARTIFACT_DIR / "artifacts" / "llamastack"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = oc(
        "get",
        "llamastackdistribution",
        "-n",
        args.namespace,
        "-o",
        "yaml",
        check=False,
        log_stdout=False,
    )
    if result.returncode == 0 and result.stdout:
        (artifacts_dir / "distribution.yaml").write_text(result.stdout, encoding="utf-8")

    result = oc(
        "get",
        "pods",
        "-n",
        args.namespace,
        "-l",
        "app=llama-stack",
        "-o",
        "wide",
        check=False,
        log_stdout=False,
    )
    if result.returncode == 0 and result.stdout:
        (artifacts_dir / "pods.txt").write_text(result.stdout, encoding="utf-8")

    return "State captured"
