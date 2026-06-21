"""
Cleanup test resources for Llama Stack benchmarks.

Migrated from tekton-benchmarks/tasks/cleanup.yaml.
Removes:
- LlamaStackDistribution CR
- PostgreSQL deployment and service
- MCP server deployment and service
- Locust jobs and services (forge-labeled)
- Optionally: InferenceService and ServingRuntime (for post-cleanup)
"""

from __future__ import annotations

import logging

from projects.core.dsl import always, entrypoint, execute_tasks, task
from projects.core.dsl.utils.k8s import oc, oc_resource_exists
from projects.core.library import env

logger = logging.getLogger(__name__)


@entrypoint
def run(
    *,
    namespace: str,
    distribution_name: str,
    model_name: str,
    cleanup_inference: bool = False,
) -> int:
    """
    Clean up test resources.

    Args:
        namespace: Target namespace
        distribution_name: LlamaStackDistribution name
        model_name: InferenceService name
        cleanup_inference: If True, also removes inference service
    """
    execute_tasks(locals())
    return 0


@task
def cleanup_locust(args, ctx):
    """Remove Locust jobs, services, and configmaps."""
    logger.info("Cleaning up Locust resources")
    oc(
        "delete",
        "jobs",
        "-n",
        args.namespace,
        "-l",
        "test=locust-llamastack",
        "--ignore-not-found=true",
        check=False,
    )
    oc(
        "delete",
        "services",
        "-n",
        args.namespace,
        "-l",
        "test=locust-llamastack",
        "--ignore-not-found=true",
        check=False,
    )
    oc(
        "delete",
        "configmap",
        "locust-scripts-llamastack",
        "-n",
        args.namespace,
        "--ignore-not-found=true",
        check=False,
    )
    return "Locust resources cleaned"


@task
def cleanup_llamastack(args, ctx):
    """Remove LlamaStackDistribution CR."""
    if oc_resource_exists(
        "llamastackdistribution", args.distribution_name, namespace=args.namespace
    ):
        logger.info("Deleting LlamaStackDistribution: %s", args.distribution_name)
        oc(
            "delete",
            "llamastackdistribution",
            args.distribution_name,
            "-n",
            args.namespace,
            "--timeout=60s",
            check=False,
        )
    return "LlamaStack cleaned"


@task
def cleanup_postgres(args, ctx):
    """Remove PostgreSQL deployment and service."""
    for resource in ("deployment/postgres-db", "service/postgres-db", "secret/postgres-secret"):
        oc("delete", resource, "-n", args.namespace, "--ignore-not-found=true", check=False)
    return "PostgreSQL cleaned"


@task
def cleanup_mcp_server(args, ctx):
    """Remove MCP server deployment and service."""
    for name in ("benchmark-mcp-server", "sdg-docs-mcp-server"):
        oc(
            "delete",
            "deployment",
            name,
            "-n",
            args.namespace,
            "--ignore-not-found=true",
            check=False,
        )
        oc("delete", "service", name, "-n", args.namespace, "--ignore-not-found=true", check=False)
    return "MCP servers cleaned"


@task
def cleanup_inference(args, ctx):
    """Remove inference service (only in post-cleanup)."""
    if not args.cleanup_inference:
        return "Inference cleanup skipped (pre-cleanup mode)"

    logger.info("Cleaning up inference service: %s", args.model_name)
    oc(
        "delete",
        "inferenceservice",
        args.model_name,
        "-n",
        args.namespace,
        "--ignore-not-found=true",
        "--timeout=120s",
        check=False,
    )
    oc(
        "delete",
        "servingruntime",
        "-n",
        args.namespace,
        "--all",
        "--ignore-not-found=true",
        check=False,
    )
    return "Inference service cleaned"


@always
@task
def capture_final_state(args, ctx):
    """Capture final cluster state for diagnostics."""
    artifacts_dir = env.ARTIFACT_DIR / "artifacts" / "cleanup"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    result = oc("get", "all", "-n", args.namespace, "-o", "wide", check=False, log_stdout=False)
    if result.returncode == 0 and result.stdout:
        (artifacts_dir / "remaining_resources.txt").write_text(result.stdout, encoding="utf-8")

    return "Final state captured"
