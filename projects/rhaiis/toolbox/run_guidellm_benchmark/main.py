#!/usr/bin/env python3

from __future__ import annotations

import json
import logging
import subprocess

from projects.core.dsl import (
    always,
    entrypoint,
    execute_tasks,
    shell,
    task,
    template,
)

logger = logging.getLogger(__name__)


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


def _oc_run(
    *args: str,
    check: bool = True,
    capture_output: bool = True,
    input_text: str | None = None,
    timeout_seconds: float = 300,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["oc", *args],
        capture_output=capture_output,
        text=True,
        input=input_text,
        timeout=timeout_seconds,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"oc {' '.join(args)} failed: {result.stderr}")
    return result


def _oc_get_json(
    kind: str,
    *,
    name: str | None = None,
    namespace: str | None = None,
    selector: str | None = None,
    ignore_not_found: bool = False,
) -> dict | None:
    cmd = ["get", kind]
    if name:
        cmd.append(name)
    if namespace:
        cmd.extend(["-n", namespace])
    if selector:
        cmd.extend(["-l", selector])
    cmd.extend(["-o", "json"])
    result = _oc_run(*cmd, check=False)
    if result.returncode != 0:
        if ignore_not_found:
            return None
        raise RuntimeError(f"oc get {kind} failed: {result.stderr}")
    return json.loads(result.stdout)


def _best_effort_delete(description: str, *oc_args: str) -> None:
    try:
        _oc_run(*oc_args, check=False, timeout_seconds=60)
    except subprocess.TimeoutExpired:
        logger.warning("Timed out deleting %s", description)


def _write_artifact(path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _poll_copy_pod_ready(pod_name, namespace, *, timeout_seconds=120, interval_seconds=5):
    import time

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        payload = _oc_get_json("pod", name=pod_name, namespace=namespace)
        if payload:
            conditions = payload.get("status", {}).get("conditions", [])
            if any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions):
                logger.info("Copy pod %s is ready", pod_name)
                return
        time.sleep(interval_seconds)
    raise RuntimeError(f"Timed out waiting for copy pod {pod_name} to be ready")


def _wait_for_job_completion(job_name, namespace, *, timeout_seconds, interval_seconds=10):
    import time

    def _job_terminal():
        payload = _oc_get_json("job", name=job_name, namespace=namespace)
        if payload is None:
            return None
        status = payload.get("status", {})
        if status.get("succeeded"):
            return payload
        for condition in status.get("conditions", []):
            if condition.get("type") == "Failed" and condition.get("status") == "True":
                raise RuntimeError(f"job/{job_name} failed: {condition.get('reason', 'unknown')}")
        if status.get("failed"):
            raise RuntimeError(f"job/{job_name} failed after {status['failed']} attempt(s)")
        return None

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            value = _job_terminal()
            if value:
                return value
        except RuntimeError:
            raise
        except Exception as exc:
            logger.info("waiting for job/%s: %s", job_name, exc)
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
    shell.run(f"oc apply -f {pvc_path}")

    job_path = src_dir / "guidellm-job.yaml"
    template.render_template_to_file("guidellm_job.yaml.j2", job_path)
    shell.run(f"oc apply -f {job_path}")

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

    result = _oc_run(
        "get", "job", context.job_name, "-n", args.namespace, "-o", "yaml", check=False
    )
    if result.returncode == 0 and result.stdout:
        _write_artifact(artifacts_dir / "guidellm_job.yaml", result.stdout)

    result = _oc_run(
        "get",
        "pods",
        "-l",
        f"job-name={context.job_name}",
        "-n",
        args.namespace,
        "-o",
        "yaml",
        check=False,
    )
    if result.returncode == 0 and result.stdout:
        _write_artifact(artifacts_dir / "guidellm_pods.yaml", result.stdout)

    result = _oc_run("logs", f"job/{context.job_name}", "-n", args.namespace, check=False)
    if result.returncode == 0 and result.stdout:
        _write_artifact(artifacts_dir / "guidellm_benchmark.log", result.stdout)

    return "Captured benchmark state and logs"


@always
@task
def copy_benchmark_results(args, context):
    pod_data = _oc_get_json(
        "pods",
        namespace=args.namespace,
        selector=f"job-name={context.job_name}",
        ignore_not_found=True,
    )
    context.node_name = None
    if pod_data and pod_data.get("items"):
        context.node_name = pod_data["items"][0].get("spec", {}).get("nodeName")

    src_dir = args.artifact_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

    copy_pod_path = src_dir / "guidellm-copy-pod.yaml"
    template.render_template_to_file("copy_pod.yaml.j2", copy_pod_path)
    shell.run(f"oc apply -f {copy_pod_path}")

    _poll_copy_pod_ready(context.copy_pod_name, args.namespace)

    result = _oc_run(
        "exec",
        "-n",
        args.namespace,
        context.copy_pod_name,
        "--",
        "cat",
        "/results/benchmarks.json",
        check=False,
    )
    if result.returncode == 0 and result.stdout:
        _write_artifact(
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
