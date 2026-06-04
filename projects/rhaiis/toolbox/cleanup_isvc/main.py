#!/usr/bin/env python3

from projects.core.dsl import (
    RetryFailure,
    entrypoint,
    execute_tasks,
    retry,
    task,
)
from projects.core.dsl.utils.k8s import oc, oc_resource_exists


@entrypoint
def run(*, name: str, namespace: str):
    return execute_tasks(locals())


@task
def delete_inference_service(args, context):
    oc(
        "delete",
        "inferenceservice",
        args.name,
        "-n",
        args.namespace,
        "--ignore-not-found",
        check=False,
    )
    return f"Deleted InferenceService {args.name}"


@task
def delete_serving_runtime(args, context):
    oc(
        "delete",
        "servingruntime",
        args.name,
        "-n",
        args.namespace,
        "--ignore-not-found",
        check=False,
    )
    return f"Deleted ServingRuntime {args.name}"


@retry(attempts=30, delay=10, retry_on_exceptions=True)
@task
def wait_for_deletion(args, context):
    if oc_resource_exists("inferenceservice", args.name, namespace=args.namespace):
        raise RetryFailure(f"InferenceService {args.name} still exists")

    if oc_resource_exists("servingruntime", args.name, namespace=args.namespace):
        raise RetryFailure(f"ServingRuntime {args.name} still exists")

    return f"Resources {args.name} fully deleted"


if __name__ == "__main__":
    run.main()
