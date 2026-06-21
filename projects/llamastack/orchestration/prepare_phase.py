"""
Prepare phase for Llama Stack performance tests.

Deploys only the vLLM/RHAIIS inference service (the slow part that stays
across test iterations). LlamaStack, Postgres, and prompts are deployed
per-iteration by the test phase for clean-state testing.

Assumes GPU operator, NFD, and RHOAI are already installed on the cluster.
"""

from __future__ import annotations

import logging

from projects.core.orchestration.utils.k8s import ensure_namespace
from projects.llamastack.orchestration.runtime_config import cfg

logger = logging.getLogger(__name__)


def run() -> int:
    namespace = cfg.get_namespace()
    preset = cfg.get_preset_name()
    model_config = cfg.get_model_config()

    logger.info("=== Llama Stack Prepare Phase ===")
    logger.info("Preset: %s", preset)
    logger.info("Namespace: %s", namespace)
    logger.info("Model: %s", model_config["name"])

    ensure_namespace(
        namespace,
        labels={
            "app.kubernetes.io/managed-by": "forge",
            "forge.openshift.io/project": "llamastack",
        },
    )

    _deploy_inference(namespace=namespace, model_config=model_config)

    logger.info("=== Prepare phase complete ===")
    return 0


def _deploy_inference(*, namespace: str, model_config: dict) -> None:
    """Deploy vLLM/RHAIIS inference service."""
    from projects.llamastack.toolbox.deploy_rhaiis import main as deploy_rhaiis_mod

    logger.info("Deploying inference: %s", model_config["name"])
    deploy_rhaiis_mod.run(
        namespace=namespace,
        model_name=model_config["name"],
        pvc_name=model_config.get("pvc_name"),
        pvc_size=model_config.get("pvc_size"),
        deploy_timeout=model_config.get("deploy_timeout", 900),
    )
