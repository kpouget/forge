#!/usr/bin/env python3

import time

from projects.core.dsl import (
    RetryFailure,
    entrypoint,
    execute_tasks,
    retry,
    task,
)
from projects.core.dsl.utils.k8s import condition_status, oc, oc_get_json


@entrypoint
def run(
    *,
    name: str,
    namespace: str,
    timeout_seconds: int = 3600,
    health_check_timeout: int = 120,
    poll_interval: int = 10,
):
    return execute_tasks(locals())


@task
def compute_retry_params(args, context):
    context.max_attempts = max(1, args.timeout_seconds // args.poll_interval)
    context.health_attempts = max(1, args.health_check_timeout // args.poll_interval)
    context.start_time = time.monotonic()
    return (
        f"Will poll every {args.poll_interval}s, "
        f"max {context.max_attempts} attempts for readiness, "
        f"{context.health_attempts} attempts for health"
    )


@retry(attempts=360, delay=10, retry_on_exceptions=True)
@task
def wait_for_ready(args, context):
    elapsed = time.monotonic() - context.start_time
    if elapsed > args.timeout_seconds:
        raise RuntimeError(
            f"InferenceService {args.name} not ready after {elapsed:.0f}s (timeout={args.timeout_seconds}s)"
        )

    isvc = oc_get_json(
        "inferenceservice", name=args.name, namespace=args.namespace, ignore_not_found=True
    )
    if isvc is None:
        raise RetryFailure(f"InferenceService {args.name} not found yet")

    ready = condition_status(isvc, "Ready")
    if ready == "True":
        return f"InferenceService {args.name} is Ready"

    conditions = isvc.get("status", {}).get("conditions", [])
    reasons = [f"{c['type']}={c.get('status', '?')}({c.get('reason', '')})" for c in conditions]
    raise RetryFailure(f"InferenceService {args.name} not ready: {', '.join(reasons)}")


@task
def verify_health(args, context):
    pod_name_result = oc(
        "get",
        "pods",
        "-l",
        f"serving.kserve.io/inferenceservice={args.name}",
        "-n",
        args.namespace,
        "-o",
        "jsonpath={.items[0].metadata.name}",
        check=False,
        log_stdout=False,
    )
    if pod_name_result.returncode != 0 or not pod_name_result.stdout.strip():
        return "No pod found — skipping health check (InferenceService is Ready)"

    pod_name = pod_name_result.stdout.strip()
    health_result = oc(
        "exec",
        pod_name,
        "-n",
        args.namespace,
        "-c",
        "kserve-container",
        "--",
        "curl",
        "-sf",
        "http://localhost:8080/health",
        check=False,
        log_stdout=False,
    )
    if health_result.returncode != 0:
        return (
            f"Health endpoint not responding on {pod_name} (InferenceService is Ready, proceeding)"
        )

    return f"Health check passed on pod {pod_name}"


if __name__ == "__main__":
    run.main()
