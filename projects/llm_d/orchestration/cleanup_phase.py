from __future__ import annotations

import logging

from projects.core.dsl.utils.k8s import resource_exists
from projects.llm_d.orchestration.runtime_config import init as runtime_init
from projects.llm_d.toolbox.cleanup_test_resources import main as cleanup_test_resources_command

logger = logging.getLogger(__name__)


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

    runtime_init()
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
    if not resource_exists("namespace", namespace):
        return

    cleanup_test_resources_command.run(
        namespace=namespace,
        inference_service_name=inference_service_name,
        smoke_job_name=None,  # No specific smoke job name for runtime cleanup
        benchmark_job_name=benchmark_name,
        cleanup_timeout_seconds=cleanup_timeout_seconds,
        cleanup_all_llm_d_resources=True,  # Enable broad cleanup for runtime
    )
