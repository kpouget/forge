"""
Deploy PostgreSQL for Llama Stack state storage.

Migrated from tekton-benchmarks/tasks/deploy-postgres.yaml.
Deploys PostgreSQL with:
- postgres-exporter sidecar for Prometheus metrics
- Secret for credentials
- Service for internal access
"""

from __future__ import annotations

import logging

from projects.core.dsl import always, entrypoint, execute_tasks, retry, task
from projects.core.dsl.utils.k8s import oc, oc_get_json
from projects.core.library import env
from projects.llamastack.orchestration.runtime_config import cfg

logger = logging.getLogger(__name__)


@entrypoint
def run(*, namespace: str) -> int:
    """
    Deploy PostgreSQL for LlamaStack.

    Args:
        namespace: Target namespace
    """
    execute_tasks(locals())
    return 0


@task
def deploy_postgres(args, ctx):
    """Deploy PostgreSQL deployment, service, and secret."""
    manifests_dir = cfg.get_manifests_dir()
    manifest_path = manifests_dir / "postgres.yaml"

    oc("apply", "-f", str(manifest_path), "-n", args.namespace, check=True)
    return "PostgreSQL manifests applied"


@retry(attempts=30, delay=10, backoff=1.0)
@task
def wait_for_ready(args, ctx):
    """Wait for PostgreSQL to be ready."""
    payload = oc_get_json(
        "deployment",
        name="postgres-db",
        namespace=args.namespace,
        ignore_not_found=True,
    )
    if not payload:
        return (False, "Waiting for postgres-db deployment")

    available = payload.get("status", {}).get("availableReplicas", 0)
    if available > 0:
        return "PostgreSQL is ready"

    return (False, "Waiting for postgres-db replicas")


@always
@task
def capture_state(args, ctx):
    """Capture PostgreSQL state for diagnostics."""
    artifacts_dir = env.ARTIFACT_DIR / "artifacts" / "postgres"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = oc(
        "get",
        "pods",
        "-n",
        args.namespace,
        "-l",
        "app=postgres",
        "-o",
        "wide",
        check=False,
        log_stdout=False,
    )
    if result.returncode == 0 and result.stdout:
        (artifacts_dir / "pods.txt").write_text(result.stdout, encoding="utf-8")

    return "State captured"
