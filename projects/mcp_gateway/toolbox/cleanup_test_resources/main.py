#!/usr/bin/env python3
"""
Clean up MCP Gateway test resources from a namespace:
- Locust Jobs, Services, ConfigMaps
- Mock server Deployment + Service
- Infrastructure (HTTPRoute, DestinationRule, MCPServerRegistration)
"""

from __future__ import annotations

import logging

from projects.core.dsl import entrypoint, execute_tasks, task
from projects.core.dsl.utils.k8s import best_effort_oc

logger = logging.getLogger(__name__)

FORGE_LABEL = "forge.openshift.io/project=mcp_gateway"
LOCUST_LABEL = "test=locust-mcp"


@entrypoint
def run(
    *,
    namespace: str,
    mock_server_name: str,
) -> int:
    """
    Remove all test-level resources from the namespace.

    Args:
        namespace: Namespace containing the test resources
        mock_server_name: Name of the mock server deployment to remove
    """
    execute_tasks(locals())
    return 0


@task
def cleanup_locust_resources(args, ctx):
    """Remove all Locust test resources (Jobs, Services, ConfigMaps)"""
    for kind in ("job", "svc", "configmap"):
        best_effort_oc(
            "delete",
            kind,
            "-n",
            args.namespace,
            "-l",
            LOCUST_LABEL,
            "--ignore-not-found=true",
            description=f"locust {kind}s",
        )
    return "Removed Locust resources"


@task
def cleanup_infrastructure(args, ctx):
    """Remove gateway infrastructure resources"""
    best_effort_oc(
        "delete",
        "mcpserverregistration",
        "--all",
        "-n",
        args.namespace,
        "--ignore-not-found=true",
        description="MCPServerRegistration",
    )
    best_effort_oc(
        "delete",
        "httproute",
        "--all",
        "-n",
        args.namespace,
        "--ignore-not-found=true",
        description="HTTPRoute",
    )
    best_effort_oc(
        "delete",
        "destinationrule",
        "-n",
        "istio-system",
        "-l",
        FORGE_LABEL,
        "--ignore-not-found=true",
        description="DestinationRule in istio-system",
    )
    return "Removed infrastructure resources"


@task
def cleanup_mock_server(args, ctx):
    """Remove mock server Deployment and Service"""
    for kind in ("deployment", "service"):
        best_effort_oc(
            "delete",
            kind,
            args.mock_server_name,
            "-n",
            args.namespace,
            "--ignore-not-found=true",
            description=f"mock server {kind}",
        )
    return f"Removed mock server {args.mock_server_name}"


@task
def cleanup_forge_labeled(args, ctx):
    """Remove any remaining resources with the forge project label"""
    for kind in ("deployment", "job", "pod", "configmap", "svc"):
        best_effort_oc(
            "delete",
            kind,
            "-n",
            args.namespace,
            "-l",
            FORGE_LABEL,
            "--ignore-not-found=true",
            description=f"forge-labeled {kind}s",
        )
    return "Removed forge-labeled resources"


if __name__ == "__main__":
    run.main()
