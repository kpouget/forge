#!/usr/bin/env python3

from projects.core.dsl import (
    RetryFailure,
    entrypoint,
    execute_tasks,
    retry,
    shell,
    task,
)


@entrypoint
def run(*, name: str, namespace: str):
    return execute_tasks(locals())


@task
def delete_inference_service(args, context):
    result = shell.run(
        f"oc delete inferenceservice {args.name} -n {args.namespace} --ignore-not-found",
        check=False,
    )
    if result.returncode != 0:
        return f"Warning: failed to delete InferenceService {args.name}: {result.stderr}"
    return f"Deleted InferenceService {args.name}"


@task
def delete_serving_runtime(args, context):
    result = shell.run(
        f"oc delete servingruntime {args.name} -n {args.namespace} --ignore-not-found",
        check=False,
    )
    if result.returncode != 0:
        return f"Warning: failed to delete ServingRuntime {args.name}: {result.stderr}"
    return f"Deleted ServingRuntime {args.name}"


@retry(attempts=30, delay=10, retry_on_exceptions=True)
@task
def wait_for_deletion(args, context):
    result = shell.run(
        f"oc get inferenceservice {args.name} -n {args.namespace}",
        check=False,
        log_stdout=False,
    )
    if result.returncode == 0:
        raise RetryFailure(f"InferenceService {args.name} still exists")

    result = shell.run(
        f"oc get servingruntime {args.name} -n {args.namespace}",
        check=False,
        log_stdout=False,
    )
    if result.returncode == 0:
        raise RetryFailure(f"ServingRuntime {args.name} still exists")

    return f"Resources {args.name} fully deleted"


if __name__ == "__main__":
    run.main()
