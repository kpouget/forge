#!/usr/bin/env python3

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from projects.core.dsl import entrypoint, execute_tasks, retry, task
from projects.core.dsl.utils import write_text
from projects.core.dsl.utils.k8s import (
    apply_manifest,
    oc,
    oc_get_json,
)
from projects.guidellm.toolbox.run_guidellm_benchmark.utils import (
    render_guidellm_copy_pod_from_parts,
    render_guidellm_job_from_parts,
    render_guidellm_pvc_from_parts,
)

logger = logging.getLogger(__name__)


@entrypoint
def run(
    *,
    namespace: str,
    benchmark: dict | None = None,
    endpoint_url: str,
) -> int:
    """
    Run the optional GuideLLM benchmark against a resolved endpoint.

    Args:
        namespace: Namespace used by llm_d
        benchmark: Optional benchmark configuration
        endpoint_url: Gateway endpoint URL returned by the deploy command
    """

    execute_tasks(locals())
    return 0


@task
def cleanup_previous_guidellm_resources_task(args, ctx):
    """Delete previous GuideLLM benchmark helper resources"""

    if not args.benchmark:
        return "GuideLLM benchmark disabled"

    benchmark_name = args.benchmark["job_name"]
    namespace = args.namespace
    _best_effort_delete(
        "GuideLLM benchmark job and pvc",
        "delete",
        "job,pvc",
        benchmark_name,
        "-n",
        namespace,
        "--ignore-not-found=true",
    )
    _best_effort_delete(
        "GuideLLM benchmark copy pod",
        "delete",
        "pod",
        f"{benchmark_name}-copy",
        "-n",
        namespace,
        "--ignore-not-found=true",
    )
    return f"Deleted previous GuideLLM resources for {benchmark_name}"


def _best_effort_delete(description: str, *oc_args: str) -> None:
    try:
        oc(*oc_args, check=False, timeout_seconds=60)
    except subprocess.TimeoutExpired:
        logger.warning("Timed out deleting %s: oc %s", description, " ".join(oc_args))


@task
def create_guidellm_resources_task(args, ctx):
    """Create the GuideLLM benchmark PVC and job"""

    if not args.benchmark:
        return "GuideLLM benchmark disabled"

    apply_manifest(
        args.artifact_dir / "src" / "guidellm-pvc.yaml",
        render_guidellm_pvc_from_parts(
            namespace=args.namespace,
            benchmark=args.benchmark,
        ),
    )
    apply_manifest(
        args.artifact_dir / "src" / "guidellm-job.yaml",
        render_guidellm_job_from_parts(
            namespace=args.namespace,
            benchmark=args.benchmark,
            endpoint_url=args.endpoint_url,
        ),
    )
    return f"GuideLLM benchmark {args.benchmark['job_name']} created"


@retry(attempts=180, delay=10, backoff=1.0)
@task
def wait_guidellm_benchmark_task(args, ctx):
    """Wait for the GuideLLM benchmark job to complete"""

    if not args.benchmark:
        return "GuideLLM benchmark disabled"

    benchmark_name = args.benchmark["job_name"]
    namespace = args.namespace

    payload = oc_get_json("job", name=benchmark_name, namespace=namespace)
    status = payload.get("status", {})
    if status.get("succeeded"):
        return f"GuideLLM benchmark {benchmark_name} completed"
    if status.get("failed"):
        raise RuntimeError(f"GuideLLM job {benchmark_name} failed")
    return False  # Retry


@task
def capture_guidellm_state_task(args, ctx):
    """Capture GuideLLM benchmark job state and logs"""

    if not args.benchmark:
        return "GuideLLM benchmark disabled"

    capture_guidellm_state(
        artifact_dir=args.artifact_dir,
        namespace=args.namespace,
        benchmark=args.benchmark,
    )
    return f"GuideLLM benchmark {args.benchmark['job_name']} state captured"


@task
def create_copy_pod(args, ctx):
    """Create copy pod for GuideLLM results"""

    if not args.benchmark:
        return "GuideLLM benchmark disabled"

    benchmark_name = args.benchmark["job_name"]
    ctx.benchmark_name = benchmark_name
    pod_data = oc_get_json(
        "pods",
        namespace=args.namespace,
        selector=f"job-name={benchmark_name}",
        ignore_not_found=True,
    )
    node_name = None
    if pod_data and pod_data.get("items"):
        node_name = pod_data["items"][0].get("spec", {}).get("nodeName")

    apply_manifest(
        args.artifact_dir / "src" / "guidellm-copy-pod.yaml",
        render_guidellm_copy_pod_from_parts(
            namespace=args.namespace,
            benchmark=args.benchmark,
            node_name=node_name,
        ),
    )
    return f"Created copy pod {benchmark_name}-copy"


@retry(attempts=24, delay=5, backoff=1.0)
@task
def wait_copy_pod_ready(args, ctx):
    """Wait for copy pod to be ready"""

    if not args.benchmark:
        return "GuideLLM benchmark disabled"

    payload = oc_get_json(
        "pod",
        name=f"{ctx.benchmark_name}-copy",
        namespace=args.namespace,
    )
    conditions = payload.get("status", {}).get("conditions", [])
    if any(
        condition.get("type") == "Ready" and condition.get("status") == "True"
        for condition in conditions
    ):
        return f"Copy pod {ctx.benchmark_name}-copy ready"
    return False  # Retry


@task
def extract_results(args, ctx):
    """Extract GuideLLM results from copy pod"""

    if not args.benchmark:
        return "GuideLLM benchmark disabled"

    result = oc(
        "exec",
        "-n",
        args.namespace,
        f"{ctx.benchmark_name}-copy",
        "--",
        "cat",
        "/results/benchmarks.json",
        check=False,
        capture_output=True,
    )
    if result.returncode == 0 and result.stdout:
        write_text(
            args.artifact_dir / "artifacts" / "results" / "benchmarks.json",
            result.stdout,
        )
        return f"Extracted results for {ctx.benchmark_name}"
    else:
        return f"No results found for {ctx.benchmark_name}"


def capture_guidellm_state(*, artifact_dir: Path, namespace: str, benchmark: dict | None) -> None:
    if not benchmark:
        return

    benchmark_name = benchmark["job_name"]
    artifacts_dir = artifact_dir / "artifacts"

    capture_get(
        "job",
        benchmark_name,
        namespace,
        "yaml",
        artifacts_dir / "guidellm_benchmark_job.yaml",
    )
    capture_get(
        "pods",
        None,
        namespace,
        "yaml",
        artifacts_dir / "guidellm_benchmark_job.pods.yaml",
        selector=f"job-name={benchmark_name}",
    )
    result = oc(
        "logs",
        f"job/{benchmark_name}",
        "-n",
        namespace,
        check=False,
        capture_output=True,
    )
    if result.returncode == 0 and result.stdout:
        write_text(artifacts_dir / "guidellm_benchmark_job.logs", result.stdout)


def capture_get(
    kind: str,
    name: str | None,
    namespace: str,
    output: str,
    destination: Path,
    *,
    selector: str | None = None,
) -> None:
    args = ["get", kind]
    if name:
        args.append(name)
    args.extend(["-n", namespace])
    if selector:
        args.extend(["-l", selector])
    args.extend(["-o", output])
    result = oc(*args, check=False, capture_output=True)
    if result.returncode == 0 and result.stdout:
        write_text(destination, result.stdout)


if __name__ == "__main__":
    run.main()
