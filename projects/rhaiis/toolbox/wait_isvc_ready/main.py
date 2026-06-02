#!/usr/bin/env python3

import json

from projects.core.dsl import (
    RetryFailure,
    entrypoint,
    execute_tasks,
    retry,
    shell,
    task,
)


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
    return (
        f"Will poll every {args.poll_interval}s, "
        f"max {context.max_attempts} attempts for readiness, "
        f"{context.health_attempts} attempts for health"
    )


@retry(attempts=360, delay=10, retry_on_exceptions=True)
@task
def wait_for_ready(args, context):
    result = shell.run(
        f"oc get inferenceservice {args.name} -n {args.namespace} -ojson",
        check=False,
        log_stdout=False,
    )
    if result.returncode != 0:
        raise RetryFailure(f"InferenceService {args.name} not found yet")

    isvc = json.loads(result.stdout)
    conditions = isvc.get("status", {}).get("conditions", [])

    ready = False
    for cond in conditions:
        if cond.get("type") == "Ready" and cond.get("status") == "True":
            ready = True
            break

    if not ready:
        reasons = [f"{c['type']}={c.get('status', '?')}({c.get('reason', '')})" for c in conditions]
        raise RetryFailure(f"InferenceService {args.name} not ready: {', '.join(reasons)}")

    return f"InferenceService {args.name} is Ready"


@task
def verify_health(args, context):
    pod_result = shell.run(
        f"oc get pods -l serving.kserve.io/inferenceservice={args.name} "
        f"-n {args.namespace} -o jsonpath='{{.items[0].metadata.name}}'",
        check=False,
        log_stdout=False,
    )
    if pod_result.returncode != 0 or not pod_result.stdout.strip():
        return "No pod found — skipping health check (InferenceService is Ready)"

    pod_name = pod_result.stdout.strip()
    health_result = shell.run(
        f"oc exec {pod_name} -n {args.namespace} -c kserve-container -- "
        f"curl -sf http://localhost:8080/health",
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
