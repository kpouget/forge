#!/usr/bin/env python3

from __future__ import annotations

import json
import logging

from projects.core.dsl import always, entrypoint, execute_tasks, retry, shell, task, template
from projects.core.dsl.utils.k8s import oc, resource_exists

logger = logging.getLogger(__name__)


@entrypoint
def run(*, clusterpolicy_name: str = "gpu-cluster-policy", timeout_seconds: int = 1800) -> int:
    """
    Bootstrap the NVIDIA ClusterPolicy used by llm_d and wait for readiness.

    Args:
        clusterpolicy_name: Name of the ClusterPolicy resource
        timeout_seconds: Maximum time to wait for the ClusterPolicy to become ready
    """

    execute_tasks(locals())
    return 0


@task
def setup_directories(args, ctx):
    """Create command source and artifact directories"""

    shell.mkdir("src")
    shell.mkdir("artifacts")
    return f"Prepared GPU ClusterPolicy bootstrap for {args.clusterpolicy_name}"


@task
def render_manifest(args, ctx):
    """Render the ClusterPolicy manifest"""

    ctx.manifest_file = args.artifact_dir / "src" / "gpu-clusterpolicy.yaml"
    template.render_template_to_file("gpu-clusterpolicy.yaml.j2", ctx.manifest_file)
    return f"Rendered ClusterPolicy manifest for {args.clusterpolicy_name}"


@task
def apply_manifest_if_missing(args, ctx):
    """Apply the ClusterPolicy manifest when missing"""

    if resource_exists("clusterpolicy", args.clusterpolicy_name):
        return f"ClusterPolicy/{args.clusterpolicy_name} already exists"

    shell.run(f"oc apply -f {ctx.manifest_file}")
    return f"Applied ClusterPolicy/{args.clusterpolicy_name}"


@retry(attempts=60, delay=15, backoff=1.0)
@task
def wait_for_clusterpolicy_ready(args, ctx):
    """Wait for the ClusterPolicy to report ready"""

    result = oc(
        "get",
        "clusterpolicy",
        args.clusterpolicy_name,
        "-o",
        "json",
        check=False,
        capture_output=True,
        log_stdout=False,
    )
    if result.returncode != 0:
        return False  # Retry

    try:
        payload = json.loads(result.stdout)
        status = payload.get("status", {})
        state = status.get("state", "").lower()

        if state == "ready":
            return f"ClusterPolicy/{args.clusterpolicy_name} ready"

        # Look for error conditions to provide useful feedback
        conditions = status.get("conditions", [])
        for condition in conditions:
            if condition.get("type") == "Error" and condition.get("status") == "True":
                message = condition.get("message", "")
                if message:
                    logger.info(f"ClusterPolicy not ready: {message}")
                    return (False, f"ClusterPolicy not ready: {message}")

        # Fallback if no specific error message
        return (False, f"ClusterPolicy state: {state}")

    except json.JSONDecodeError:
        return False  # Retry on JSON parse error


@always
@task
def capture_clusterpolicy_state(args, ctx):
    """Capture ClusterPolicy YAML for diagnostics"""

    shell.run(
        f"oc get clusterpolicy {args.clusterpolicy_name} -oyaml",
        stdout_dest=args.artifact_dir / "artifacts" / "clusterpolicy.yaml",
        check=False,
    )
    return f"Captured ClusterPolicy/{args.clusterpolicy_name} state"


if __name__ == "__main__":
    run.main()
