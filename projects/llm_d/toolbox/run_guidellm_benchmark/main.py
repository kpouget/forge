#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path

from projects.core.dsl import execute_tasks, task, toolbox
from projects.llm_d.runtime import llmd_runtime, phase_inputs


def run(
    *,
    config_dir: str,
    preset_name: str,
    namespace: str,
    platform: dict,
    model_key: str,
    model: dict,
    scheduler_profile_key: str,
    scheduler_profile: dict | None,
    model_cache: dict,
    smoke_request: dict,
    benchmark: dict | None = None,
    endpoint_url: str,
) -> int:
    """
    Run the optional GuideLLM benchmark against a resolved endpoint.

    Args:
        config_dir: Configuration directory
        preset_name: Selected preset name
        namespace: Namespace used by llm_d
        platform: Platform configuration
        model_key: Selected model key
        model: Selected model configuration
        scheduler_profile_key: Scheduler profile key
        scheduler_profile: Scheduler profile configuration
        model_cache: Model-cache configuration
        smoke_request: Smoke-request configuration
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
    llmd_runtime.oc(
        "delete",
        "job,pvc",
        benchmark_name,
        "-n",
        namespace,
        "--ignore-not-found=true",
        check=False,
    )
    llmd_runtime.oc(
        "delete",
        "pod",
        f"{benchmark_name}-copy",
        "-n",
        namespace,
        "--ignore-not-found=true",
        check=False,
    )
    return f"Deleted previous GuideLLM resources for {benchmark_name}"


@task
def create_guidellm_resources_task(args, ctx):
    """Create the GuideLLM benchmark PVC and job"""

    if not args.benchmark:
        return "GuideLLM benchmark disabled"

    config = phase_inputs.build_test_inputs(
        artifact_dir=args.artifact_dir,
        config_dir=args.config_dir,
        preset_name=args.preset_name,
        namespace=args.namespace,
        platform=args.platform,
        model_key=args.model_key,
        model=args.model,
        scheduler_profile_key=args.scheduler_profile_key,
        scheduler_profile=args.scheduler_profile,
        model_cache=args.model_cache,
        smoke_request=args.smoke_request,
        benchmark=args.benchmark,
    )
    llmd_runtime.apply_manifest(
        config.artifact_dir / "src" / "guidellm-pvc.yaml",
        llmd_runtime.render_guidellm_pvc(config),
    )
    llmd_runtime.apply_manifest(
        config.artifact_dir / "src" / "guidellm-job.yaml",
        llmd_runtime.render_guidellm_job(config, args.endpoint_url),
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

    config = phase_inputs.build_test_inputs(
        artifact_dir=args.artifact_dir,
        config_dir=args.config_dir,
        preset_name=args.preset_name,
        namespace=args.namespace,
        platform=args.platform,
        model_key=args.model_key,
        model=args.model,
        scheduler_profile_key=args.scheduler_profile_key,
        scheduler_profile=args.scheduler_profile,
        model_cache=args.model_cache,
        smoke_request=args.smoke_request,
        benchmark=args.benchmark,
    )
    capture_guidellm_state(config)
    return f"GuideLLM benchmark {args.benchmark['job_name']} state captured"


@task
def copy_guidellm_results_task(args, ctx):
    """Copy GuideLLM benchmark results into artifacts"""

    if not args.benchmark:
        return "GuideLLM benchmark disabled"

    config = phase_inputs.build_test_inputs(
        artifact_dir=args.artifact_dir,
        config_dir=args.config_dir,
        preset_name=args.preset_name,
        namespace=args.namespace,
        platform=args.platform,
        model_key=args.model_key,
        model=args.model,
        scheduler_profile_key=args.scheduler_profile_key,
        scheduler_profile=args.scheduler_profile,
        model_cache=args.model_cache,
        smoke_request=args.smoke_request,
        benchmark=args.benchmark,
    )
    copy_guidellm_results(config)
    return f"GuideLLM benchmark {args.benchmark['job_name']} results copied"


def copy_guidellm_results(config: phase_inputs.TestInputs) -> None:
    if not config.benchmark:
        return

    benchmark_name = config.benchmark["job_name"]
    namespace = config.namespace
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
        config.artifact_dir / "src" / "guidellm-copy-pod.yaml",
        llmd_runtime.render_guidellm_copy_pod(config, node_name=node_name),
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
        llmd_runtime.write_text(
            config.artifact_dir / "artifacts" / "results" / "benchmarks.json",
            result.stdout,
        )


def capture_guidellm_state(config: phase_inputs.TestInputs) -> None:
    if not config.benchmark:
        return

    benchmark_name = config.benchmark["job_name"]
    namespace = config.namespace
    artifacts_dir = config.artifact_dir / "artifacts"

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
        llmd_runtime.write_text(artifacts_dir / "guidellm_benchmark_job.logs", result.stdout)


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
        llmd_runtime.write_text(destination, result.stdout)


main = toolbox.create_toolbox_main(run)


if __name__ == "__main__":
    main()
