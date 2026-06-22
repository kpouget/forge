"""
Prepare phase for MCP Gateway performance tests.

1. Clone platform manifests from the upstream mcp-gateway GitHub repo
2. Install the MCP Gateway platform at the version specified by MCP_GATEWAY_VERSION
3. Ensure test namespace exists

Usage:
    MCP_GATEWAY_VERSION=0.7.0 python -m projects.mcp_gateway.orchestration.ci prepare
"""

from __future__ import annotations

import logging
import os

from projects.core.library import config
from projects.core.orchestration.utils.k8s import ensure_namespace
from projects.mcp_gateway.orchestration.runtime_config import cfg
from projects.mcp_gateway.toolbox.install_platform import main as install_platform_mod
from projects.mcp_gateway.toolbox.platform_helpers import clone_platform_repo

logger = logging.getLogger(__name__)


def run() -> int:
    version = config.project.get_config(
        "infrastructure.mcp_gateway_version", None, print=False, warn=False
    ) or os.environ.get("MCP_GATEWAY_VERSION")
    if not version:
        raise RuntimeError(
            "MCP Gateway version not set. Use /version directive in PR comment, "
            "set infrastructure.mcp_gateway_version in config, or set "
            "MCP_GATEWAY_VERSION environment variable."
        )

    namespace = cfg.get_namespace()

    logger.info("=== MCP Gateway Prepare Phase ===")
    logger.info("Version: %s", version)
    logger.info("Namespace: %s", namespace)

    platform_cfg = cfg.get_platform_config()
    platform_cfg.setdefault("mcp_gateway_instance", {})["version"] = version

    if not platform_cfg.get("kustomize_base"):
        repo_url = platform_cfg.get("platform_repo")
        subdir = platform_cfg.get("platform_repo_subdir")
        clone_kwargs: dict = {"version": version}
        if repo_url:
            clone_kwargs["repo_url"] = repo_url
        if subdir:
            clone_kwargs["subdir"] = subdir
        kustomize_base = clone_platform_repo(**clone_kwargs)
        platform_cfg["kustomize_base"] = str(kustomize_base)

    install_platform_mod.run(platform_config=platform_cfg)

    ensure_namespace(
        namespace,
        labels={
            "app.kubernetes.io/managed-by": "forge",
            "forge.openshift.io/project": "mcp_gateway",
        },
    )

    logger.info("=== Prepare phase complete ===")
    return 0
