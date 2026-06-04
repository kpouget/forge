#!/usr/bin/env python3

from __future__ import annotations

import logging
import time

from projects.core.dsl import (
    always,
    entrypoint,
    execute_tasks,
    task,
    template,
)
from projects.core.dsl.utils import write_text
from projects.core.dsl.utils.k8s import (
    condition_status,
    oc,
    oc_get_json,
)

logger = logging.getLogger("TOOLBOX")


@entrypoint
def run(
    *,
    namespace: str,
    deployment_name: str,
    endpoint_url: str,
    model_id: str,
    data: str,
    rates: str = "1",
    max_seconds: int = 180,
    benchmark_image: str = "ghcr.io/vllm-project/guidellm:v0.6.0",
    backend_type: str = "openai_http",
    rate_type: str = "concurrent",
    timeout: int = 900,
    pvc_size: str = "5Gi",
):
    return execute_tasks(locals())


def _best_effort_delete(description: str, *oc_args: str) -> None:
    logger.info("Deleting %s ...", description)
    oc(*oc_args, check=False)


def _poll_copy_pod_ready(pod_name, namespace, *, timeout_seconds=120, interval_seconds=5):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        phase = oc(
            "get",
            "pod",
            pod_name,
            "-n",
            namespace,
            "-o",
            "jsonpath={.status.phase}",
            check=False,
            log_stdout=False,
        )
        if phase.returncode == 0 and phase.stdout.strip() == "Running":
            ready = condition_status(
                oc_get_json("pod", name=pod_name, namespace=namespace) or {},
                "Ready",
            )
            if ready == "True":
                logger.info("Copy pod %s is Running and Ready", pod_name)
                return
        logger.info("Waiting for copy pod %s ... (phase=%s)", pod_name, phase.stdout.strip())
        time.sleep(interval_seconds)
    raise RuntimeError(f"Timed out waiting for copy pod {pod_name} to be ready")


def _wait_for_job_completion(job_name, namespace, *, timeout_seconds, interval_seconds=10):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        phase = oc(
            "get",
            "job",
            job_name,
            "-n",
            namespace,
            "-o",
            "jsonpath={.status.succeeded},{.status.failed}",
            check=False,
            log_stdout=False,
        )
        if phase.returncode != 0:
            logger.info("Waiting for job/%s ... (not found yet)", job_name)
            time.sleep(interval_seconds)
            continue

        parts = phase.stdout.strip().split(",")
        succeeded = parts[0] if len(parts) > 0 else ""
        failed = parts[1] if len(parts) > 1 else ""

        if succeeded and int(succeeded) > 0:
            logger.info("job/%s succeeded", job_name)
            return

        if failed and int(failed) > 0:
            reason = oc(
                "get",
                "job",
                job_name,
                "-n",
                namespace,
                "-o",
                "jsonpath={.status.conditions[?(@.type=='Failed')].reason}",
                check=False,
                log_stdout=False,
            )
            raise RuntimeError(f"job/{job_name} failed: {reason.stdout.strip() or 'unknown'}")

        logger.info(
            "Waiting for job/%s ... (succeeded=%s, failed=%s)",
            job_name,
            succeeded or "0",
            failed or "0",
        )
        time.sleep(interval_seconds)

    raise RuntimeError(f"Timed out waiting for job/{job_name} completion in {namespace}")


@task
def cleanup_previous_resources(args, context):
    context.job_name = f"guidellm-{args.deployment_name}"
    context.pvc_name = context.job_name
    context.copy_pod_name = f"{context.job_name}-copy"

    _best_effort_delete(
        "previous benchmark job and PVC",
        "delete",
        "job,pvc",
        context.job_name,
        "-n",
        args.namespace,
        "--ignore-not-found=true",
    )
    _best_effort_delete(
        "previous copy pod",
        "delete",
        "pod",
        context.copy_pod_name,
        "-n",
        args.namespace,
        "--ignore-not-found=true",
    )
    return f"Cleaned up previous resources for {context.job_name}"


@task
def create_benchmark_resources(args, context):
    src_dir = args.artifact_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

    pvc_path = src_dir / "guidellm-pvc.yaml"
    template.render_template_to_file("guidellm_pvc.yaml.j2", pvc_path)
    oc("apply", "-f", str(pvc_path))

    job_path = src_dir / "guidellm-job.yaml"
    template.render_template_to_file("guidellm_job.yaml.j2", job_path)
    oc("apply", "-f", str(job_path))

    return f"Created PVC and benchmark job {context.job_name}"


@task
def wait_for_completion(args, context):
    _wait_for_job_completion(
        context.job_name,
        args.namespace,
        timeout_seconds=args.timeout,
    )
    return f"Benchmark job {context.job_name} completed successfully"


@always
@task
def capture_benchmark_state(args, context):
    artifacts_dir = args.artifact_dir / "artifacts"

    result = oc(
        "get",
        "job",
        context.job_name,
        "-n",
        args.namespace,
        "-o",
        "yaml",
        check=False,
        log_stdout=False,
    )
    if result.returncode == 0 and result.stdout:
        write_text(artifacts_dir / "guidellm_job.yaml", result.stdout)

    result = oc(
        "get",
        "pods",
        "-l",
        f"job-name={context.job_name}",
        "-n",
        args.namespace,
        "-o",
        "yaml",
        check=False,
        log_stdout=False,
    )
    if result.returncode == 0 and result.stdout:
        write_text(artifacts_dir / "guidellm_pods.yaml", result.stdout)

    result = oc(
        "logs",
        f"job/{context.job_name}",
        "-n",
        args.namespace,
        check=False,
        log_stdout=False,
    )
    if result.returncode == 0 and result.stdout:
        write_text(artifacts_dir / "guidellm_benchmark.log", result.stdout)

    return "Captured benchmark state and logs"


@always
@task
def copy_benchmark_results(args, context):
    pod_data = oc_get_json(
        "pods",
        namespace=args.namespace,
        selector=f"job-name={context.job_name}",
        ignore_not_found=True,
    )
    context.node_name = None
    if pod_data and pod_data.get("items"):
        context.node_name = pod_data["items"][0].get("spec", {}).get("nodeName")
        logger.info("Benchmark pod ran on node %s", context.node_name)

    src_dir = args.artifact_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

    copy_pod_path = src_dir / "guidellm-copy-pod.yaml"
    template.render_template_to_file("copy_pod.yaml.j2", copy_pod_path)
    oc("apply", "-f", str(copy_pod_path))

    _poll_copy_pod_ready(context.copy_pod_name, args.namespace)

    result = oc(
        "exec",
        "-n",
        args.namespace,
        context.copy_pod_name,
        "--",
        "cat",
        "/results/benchmarks.json",
        check=False,
        log_stdout=False,
    )
    if result.returncode == 0 and result.stdout:
        write_text(
            args.artifact_dir / "artifacts" / "results" / "benchmarks.json",
            result.stdout,
        )
        return "Copied benchmark results to artifacts"

    return "No benchmark results found on PVC"


@always
@task
def cleanup_benchmark_resources(args, context):
    _best_effort_delete(
        "benchmark job and PVC",
        "delete",
        "job,pvc",
        context.job_name,
        "-n",
        args.namespace,
        "--ignore-not-found=true",
    )
    _best_effort_delete(
        "copy pod",
        "delete",
        "pod",
        context.copy_pod_name,
        "-n",
        args.namespace,
        "--ignore-not-found=true",
    )
    return f"Cleaned up {context.job_name} resources"


if __name__ == "__main__":
    run.main()
