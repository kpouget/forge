from __future__ import annotations

import logging
import subprocess

from projects.llm_d.runtime import llmd_runtime

LOGGER = logging.getLogger(__name__)


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
    cleanup_namespace(
        namespace=namespace,
        inference_service_name=inference_service_name,
        cleanup_timeout_seconds=cleanup_timeout_seconds,
        benchmark_name=benchmark_name,
    )
    return 0


def cleanup_namespace(
    *,
    namespace: str,
    inference_service_name: str,
    cleanup_timeout_seconds: int,
    benchmark_name: str | None = None,
) -> None:
    if not llmd_runtime.resource_exists("namespace", namespace):
        return

    benchmark_names = {"guidellm-benchmark"}
    if benchmark_name:
        benchmark_names.add(benchmark_name)

    _best_effort_delete(
        "llminferenceservice",
        "delete",
        "llminferenceservice",
        inference_service_name,
        "-n",
        namespace,
        "--ignore-not-found=true",
    )

    for current_benchmark_name in sorted(benchmark_names):
        _best_effort_delete(
            "benchmark helper job and pvc",
            "delete",
            "job,pvc",
            current_benchmark_name,
            "-n",
            namespace,
            "--ignore-not-found=true",
        )
        _best_effort_delete(
            "benchmark helper copy pod",
            "delete",
            "pod",
            f"{current_benchmark_name}-copy",
            "-n",
            namespace,
            "--ignore-not-found=true",
        )

    _best_effort_delete(
        "llm_d jobs",
        "delete",
        "job",
        "-n",
        namespace,
        "-l",
        "forge.openshift.io/project=llm_d",
        "--ignore-not-found=true",
    )
    _best_effort_delete(
        "llm_d pods",
        "delete",
        "pod",
        "-n",
        namespace,
        "-l",
        "forge.openshift.io/project=llm_d",
        "--ignore-not-found=true",
    )
    _best_effort_delete(
        "llm_d non-preserved pvcs",
        "delete",
        "pvc",
        "-n",
        namespace,
        "-l",
        "forge.openshift.io/project=llm_d,forge.openshift.io/preserve!=true",
        "--ignore-not-found=true",
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


def _best_effort_delete(description: str, *oc_args: str) -> None:
    try:
        llmd_runtime.oc(*oc_args, check=False, timeout_seconds=60)
    except subprocess.TimeoutExpired:
        LOGGER.warning("Timed out deleting %s: oc %s", description, " ".join(oc_args))


def _llm_d_pods_gone(namespace: str, inference_service_name: str) -> bool:
    payload = llmd_runtime.oc_get_json(
        "pods",
        namespace=namespace,
        selector=f"app.kubernetes.io/name={inference_service_name}",
        ignore_not_found=True,
    )
    return not payload or not payload.get("items")
