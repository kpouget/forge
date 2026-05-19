from __future__ import annotations

from projects.core.dsl import execute_tasks, shell, task
from projects.llm_d.runtime import llmd_runtime, phase_inputs


def run(
    *,
    namespace: str,
    inference_service_name: str,
    cleanup_timeout_seconds: int,
    benchmark_name: str | None = None,
) -> int:
    """Delete llm_d runtime leftovers from a namespace.

    Args:
        namespace: Namespace to clean
        inference_service_name: Inference-service resource name
        cleanup_timeout_seconds: Cleanup timeout in seconds
        benchmark_name: Optional GuideLLM benchmark job name
    """

    llmd_runtime.init()
    execute_tasks(locals())
    return 0


@task
def delete_leftovers(args, ctx):
    """Delete llm_d runtime leftovers"""

    if not llmd_runtime.resource_exists("namespace", args.namespace):
        return f"Namespace {args.namespace} does not exist; nothing to clean"

    inference_service_name = args.inference_service_name
    namespace = args.namespace
    cleanup_timeout_seconds = args.cleanup_timeout_seconds
    benchmark_names = {"guidellm-benchmark"}
    if args.benchmark_name:
        benchmark_names.add(args.benchmark_name)

    shell.run(
        f"oc delete llminferenceservice {inference_service_name} "
        f"-n {namespace} --ignore-not-found=true",
        check=False,
    )

    for benchmark_name in sorted(benchmark_names):
        shell.run(
            f"oc delete job,pvc {benchmark_name} -n {namespace} --ignore-not-found=true",
            check=False,
        )
        shell.run(
            f"oc delete pod {benchmark_name}-copy -n {namespace} --ignore-not-found=true",
            check=False,
        )

    shell.run(
        f'oc delete job -n {namespace} -l "forge.openshift.io/project=llm_d" '
        "--ignore-not-found=true",
        check=False,
    )
    shell.run(
        f'oc delete pod -n {namespace} -l "forge.openshift.io/project=llm_d" '
        "--ignore-not-found=true",
        check=False,
    )
    shell.run(
        f"oc delete pvc -n {namespace} "
        '-l "forge.openshift.io/project=llm_d,forge.openshift.io/preserve!=true" '
        "--ignore-not-found=true",
        check=False,
    )

    llmd_runtime.wait_until(
        f"llminferenceservice/{inference_service_name} deletion in {namespace}",
        timeout_seconds=cleanup_timeout_seconds,
        interval_seconds=10,
        predicate=lambda: (
            not llmd_runtime.resource_exists(
                "llminferenceservice", inference_service_name, namespace=namespace
            )
        ),
    )

    llmd_runtime.wait_until(
        f"llm-d workload pods deletion in {namespace}",
        timeout_seconds=cleanup_timeout_seconds,
        interval_seconds=10,
        predicate=lambda: _llm_d_pods_gone(namespace, inference_service_name),
    )

    return f"Cleanup finished for namespace {namespace}"


def delete_run_leftovers(inputs: phase_inputs.CleanupInputs) -> None:
    if not llmd_runtime.resource_exists("namespace", inputs.namespace):
        return

    inference_service_name = inputs.platform["inference_service"]["name"]
    namespace = inputs.namespace
    cleanup_timeout_seconds = inputs.platform["cluster"]["cleanup_timeout_seconds"]
    benchmark_names = {"guidellm-benchmark"}
    if inputs.benchmark:
        benchmark_names.add(inputs.benchmark["job_name"])

    shell.run(
        f"oc delete llminferenceservice {inference_service_name} "
        f"-n {namespace} --ignore-not-found=true",
        check=False,
    )

    for benchmark_name in sorted(benchmark_names):
        shell.run(
            f"oc delete job,pvc {benchmark_name} -n {namespace} --ignore-not-found=true",
            check=False,
        )
        shell.run(
            f"oc delete pod {benchmark_name}-copy -n {namespace} --ignore-not-found=true",
            check=False,
        )

    shell.run(
        f'oc delete job -n {namespace} -l "forge.openshift.io/project=llm_d" '
        "--ignore-not-found=true",
        check=False,
    )
    shell.run(
        f'oc delete pod -n {namespace} -l "forge.openshift.io/project=llm_d" '
        "--ignore-not-found=true",
        check=False,
    )
    shell.run(
        f"oc delete pvc -n {namespace} "
        '-l "forge.openshift.io/project=llm_d,forge.openshift.io/preserve!=true" '
        "--ignore-not-found=true",
        check=False,
    )

    llmd_runtime.wait_until(
        f"llminferenceservice/{inference_service_name} deletion in {namespace}",
        timeout_seconds=cleanup_timeout_seconds,
        interval_seconds=10,
        predicate=lambda: (
            not llmd_runtime.resource_exists(
                "llminferenceservice", inference_service_name, namespace=namespace
            )
        ),
    )

    llmd_runtime.wait_until(
        f"llm-d workload pods deletion in {namespace}",
        timeout_seconds=cleanup_timeout_seconds,
        interval_seconds=10,
        predicate=lambda: _llm_d_pods_gone(namespace, inference_service_name),
    )


def _llm_d_pods_gone(namespace: str, inference_service_name: str) -> bool:
    payload = llmd_runtime.oc_get_json(
        "pods",
        namespace=namespace,
        selector=f"app.kubernetes.io/name={inference_service_name}",
        ignore_not_found=True,
    )
    return not payload or not payload.get("items")
