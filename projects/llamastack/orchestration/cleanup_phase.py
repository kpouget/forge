"""
Cleanup phases for Llama Stack performance tests.

run() = pre-cleanup (test resources only):
- Locust Jobs, Services, ConfigMaps
- LlamaStack distribution
- PostgreSQL
- MCP server

run_platform_cleanup() = post-cleanup (full teardown):
- All test resources (same as pre-cleanup)
- RHAIIS inference service
- Namespace deletion
"""

from __future__ import annotations

import logging

from projects.core.dsl.utils.k8s import oc, oc_resource_exists
from projects.llamastack.orchestration.runtime_config import cfg

logger = logging.getLogger(__name__)


def run() -> int:
    """Pre-cleanup: remove test-level resources only."""
    namespace = cfg.get_namespace()

    logger.info("=== Llama Stack Pre-Cleanup Phase ===")
    logger.info("Namespace: %s", namespace)

    if not oc_resource_exists("namespace", namespace):
        logger.info("Namespace %s does not exist, nothing to clean", namespace)
        return 0

    from projects.llamastack.toolbox.cleanup_test_resources import main as cleanup_mod

    distribution_name = cfg.get_distribution_name()
    model_config = cfg.get_model_config()

    cleanup_mod.run(
        namespace=namespace,
        distribution_name=distribution_name,
        model_name=model_config["name"],
        cleanup_inference=False,
    )

    logger.info("=== Pre-cleanup phase complete ===")
    return 0


def run_platform_cleanup() -> int:
    """Post-cleanup: remove all resources and delete the namespace."""
    namespace = cfg.get_namespace()
    distribution_name = cfg.get_distribution_name()
    model_config = cfg.get_model_config()

    logger.info("=== Llama Stack Post-Cleanup Phase ===")

    if oc_resource_exists("namespace", namespace):
        from projects.llamastack.toolbox.cleanup_test_resources import main as cleanup_mod

        cleanup_mod.run(
            namespace=namespace,
            distribution_name=distribution_name,
            model_name=model_config["name"],
            cleanup_inference=True,
        )

    if oc_resource_exists("namespace", namespace):
        logger.info("Deleting test namespace %s", namespace)
        oc("delete", "namespace", namespace, "--timeout=120s", check=False)

    logger.info("=== Post-cleanup phase complete ===")
    return 0
