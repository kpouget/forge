#!/usr/bin/env python3

from __future__ import annotations

import subprocess
from pathlib import Path

from projects.core.dsl import execute_tasks, task, toolbox
from projects.llm_d.runtime import llmd_runtime
from projects.llm_d.toolbox import toolbox_helper


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

    llmd_runtime.init()
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
        llmd_runtime.oc(*oc_args, check=False, timeout_seconds=60)
    except subprocess.TimeoutExpired:
        llmd_runtime.logger.warning("Timed out deleting %s: oc %s", description, " ".join(oc_args))


@task
def create_guidellm_resources_task(args, ctx):
    """Create the GuideLLM benchmark PVC and job"""

    if not args.benchmark:
        return "GuideLLM benchmark disabled"

    llmd_runtime.apply_manifest(
        args.artifact_dir / "src" / "guidellm-pvc.yaml",
        llmd_runtime.render_guidellm_pvc_from_parts(
            namespace=args.namespace,
            benchmark=args.benchmark,
        ),
    )
    llmd_runtime.apply_manifest(
        args.artifact_dir / "src" / "guidellm-job.yaml",
        llmd_runtime.render_guidellm_job_from_parts(
            namespace=args.namespace,
            benchmark=args.benchmark,
            endpoint_url=args.endpoint_url,
        ),
    )
    return f"GuideLLM benchmark {args.benchmark['job_name']} created"


@task
def wait_guidellm_benchmark_task(args, ctx):
    """Wait for the GuideLLM benchmark job to complete"""

    if not args.benchmark:
        return "GuideLLM benchmark disabled"

    benchmark_name = args.benchmark["job_name"]
    namespace = args.namespace

    def _job_terminal() -> dict[str, object] | None:
        payload = llmd_runtime.oc_get_json("job", name=benchmark_name, namespace=namespace)
        status = payload.get("status", {})
        if status.get("succeeded"):
            return payload
        if status.get("failed"):
            raise RuntimeError(f"GuideLLM job {benchmark_name} failed")
        return None

    llmd_runtime.wait_until(
        f"GuideLLM job/{benchmark_name}",
        timeout_seconds=args.benchmark["timeout_seconds"],
        interval_seconds=10,
        predicate=_job_terminal,
    )
    return f"GuideLLM benchmark {args.benchmark['job_name']} completed"


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
def copy_guidellm_results_task(args, ctx):
    """Copy GuideLLM benchmark results into artifacts"""

    if not args.benchmark:
        return "GuideLLM benchmark disabled"

    copy_guidellm_results(
        artifact_dir=args.artifact_dir,
        namespace=args.namespace,
        benchmark=args.benchmark,
    )
    return f"GuideLLM benchmark {args.benchmark['job_name']} results copied"


def copy_guidellm_results(*, artifact_dir: Path, namespace: str, benchmark: dict | None) -> None:
    if not benchmark:
        return

    benchmark_name = benchmark["job_name"]
    pod_data = llmd_runtime.oc_get_json(
        "pods",
        namespace=namespace,
        selector=f"job-name={benchmark_name}",
        ignore_not_found=True,
    )
    node_name = None
    if pod_data and pod_data.get("items"):
        node_name = pod_data["items"][0].get("spec", {}).get("nodeName")

    llmd_runtime.apply_manifest(
        artifact_dir / "src" / "guidellm-copy-pod.yaml",
        llmd_runtime.render_guidellm_copy_pod_from_parts(
            namespace=namespace,
            benchmark=benchmark,
            node_name=node_name,
        ),
    )

    def _helper_ready() -> bool:
        payload = llmd_runtime.oc_get_json(
            "pod",
            name=f"{benchmark_name}-copy",
            namespace=namespace,
        )
        conditions = payload.get("status", {}).get("conditions", [])
        return any(
            condition.get("type") == "Ready" and condition.get("status") == "True"
            for condition in conditions
        )

    llmd_runtime.wait_until(
        f"GuideLLM copy helper pod/{benchmark_name}-copy",
        timeout_seconds=120,
        interval_seconds=5,
        predicate=_helper_ready,
    )

    result = llmd_runtime.oc(
        "exec",
        "-n",
        namespace,
        f"{benchmark_name}-copy",
        "--",
        "cat",
        "/results/benchmarks.json",
        check=False,
        capture_output=True,
    )
    if result.returncode == 0 and result.stdout:
        toolbox_helper.write_text(
            artifact_dir / "artifacts" / "results" / "benchmarks.json",
            result.stdout,
        )


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
    result = llmd_runtime.oc(
        "logs",
        f"job/{benchmark_name}",
        "-n",
        namespace,
        check=False,
        capture_output=True,
    )
    if result.returncode == 0 and result.stdout:
        toolbox_helper.write_text(artifacts_dir / "guidellm_benchmark_job.logs", result.stdout)


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
    result = llmd_runtime.oc(*args, check=False, capture_output=True)
    if result.returncode == 0 and result.stdout:
        toolbox_helper.write_text(destination, result.stdout)


main = toolbox.create_toolbox_main(run)


if __name__ == "__main__":
    main()
