"""
Apply MCP Gateway infrastructure for mock servers.

Generates and applies HTTPRoute + DestinationRule + MCPServerRegistration
for 1..N mock servers, then waits for all registrations to become Ready.

This is MCP Gateway-specific logic — it creates CRDs that only exist in
the MCP Gateway product (MCPServerRegistration, etc).
"""

from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path

import yaml

from projects.agentic_tools.mcp.toolbox.deploy_mock_servers.main import SCALE_OUT_LABEL
from projects.core.dsl.utils.k8s import oc

logger = logging.getLogger(__name__)

DEFAULT_API_GROUP = "mcp.kuadrant.io"


def apply_infrastructure(
    *,
    namespace: str,
    count: int,
    name_prefix: str = "mock-server",
    gateway_namespace: str = "gateway-system",
    gateway_name: str = "mcp-gateway",
    api_group: str = DEFAULT_API_GROUP,
) -> None:
    """
    Generate and apply HTTPRoute + DestinationRule + MCPServerRegistration
    for `count` mock servers.
    """
    logger.info("Applying infrastructure for %d server(s) (api_group=%s)...", count, api_group)

    all_manifests = []
    for i in range(1, count + 1):
        server_name = f"{name_prefix}-{i}"
        hostname = f"server{i}.mcp.local"
        tool_prefix = f"server{i}_"

        manifest = _generate_infrastructure_manifest(
            server_name=server_name,
            hostname=hostname,
            tool_prefix=tool_prefix,
            namespace=namespace,
            gateway_namespace=gateway_namespace,
            gateway_name=gateway_name,
            api_group=api_group,
        )
        all_manifests.append(manifest)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tmp:
        tmp.write("---\n".join(all_manifests))
        tmp_path = tmp.name
    oc("apply", "-f", tmp_path)
    Path(tmp_path).unlink(missing_ok=True)

    logger.info("Infrastructure applied for %d server(s)", count)


def wait_for_registrations(
    *,
    namespace: str,
    count: int,
    name_prefix: str = "mock-server",
    timeout_seconds: int = 300,
    api_group: str = DEFAULT_API_GROUP,
) -> None:
    """Wait until all N MCPServerRegistrations report Ready."""
    logger.info("Waiting for %d MCPServerRegistration(s) to become Ready...", count)

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        ready_count = _count_ready_registrations(namespace=namespace, api_group=api_group)
        elapsed = int(timeout_seconds - (deadline - time.time()))
        logger.info(
            "  registrations ready: %d/%d (%ds elapsed)",
            ready_count,
            count,
            elapsed,
        )
        if ready_count >= count:
            logger.info("All %d registrations are Ready", count)
            return
        time.sleep(5)

    ready_count = _count_ready_registrations(namespace=namespace, api_group=api_group)
    if ready_count < count:
        raise RuntimeError(
            f"Timed out waiting for MCPServerRegistrations: "
            f"{ready_count}/{count} ready after {timeout_seconds}s"
        )


def cleanup_infrastructure(*, namespace: str, api_group: str = DEFAULT_API_GROUP) -> None:
    """Delete all scale-out infrastructure resources by label."""
    logger.info("Cleaning up infrastructure (label=%s)...", SCALE_OUT_LABEL)
    oc(
        "delete",
        f"mcpserverregistrations.{api_group},httproute",
        "-n",
        namespace,
        "-l",
        SCALE_OUT_LABEL,
        "--wait=false",
        "--ignore-not-found=true",
        check=False,
    )
    oc(
        "delete",
        "destinationrule",
        "-n",
        "istio-system",
        "-l",
        SCALE_OUT_LABEL,
        "--wait=false",
        "--ignore-not-found=true",
        check=False,
    )
    logger.info("Infrastructure cleanup complete")


def _count_ready_registrations(*, namespace: str, api_group: str = DEFAULT_API_GROUP) -> int:
    """Count MCPServerRegistrations with Ready=True condition."""
    result = oc(
        "get",
        f"mcpserverregistrations.{api_group}",
        "-n",
        namespace,
        "-l",
        SCALE_OUT_LABEL,
        "-o",
        'jsonpath={range .items[*]}{.status.conditions[?(@.type=="Ready")].status}{"\\n"}{end}',
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return 0
    return sum(1 for line in result.stdout.strip().split("\n") if line.strip() == "True")


def _generate_infrastructure_manifest(
    *,
    server_name: str,
    hostname: str,
    tool_prefix: str,
    namespace: str,
    gateway_namespace: str,
    gateway_name: str,
    api_group: str = DEFAULT_API_GROUP,
) -> str:
    """Generate YAML manifest for HTTPRoute + DestinationRule + MCPServerRegistration."""
    labels = {"experiment": "scale-out"}

    httproute = {
        "apiVersion": "gateway.networking.k8s.io/v1",
        "kind": "HTTPRoute",
        "metadata": {
            "name": f"{server_name}-route",
            "namespace": namespace,
            "labels": labels,
        },
        "spec": {
            "parentRefs": [
                {
                    "name": gateway_name,
                    "namespace": gateway_namespace,
                }
            ],
            "hostnames": [hostname],
            "rules": [
                {
                    "matches": [{"path": {"type": "PathPrefix", "value": "/mcp"}}],
                    "backendRefs": [{"name": server_name, "port": 8080}],
                }
            ],
        },
    }

    destination_rule = {
        "apiVersion": "networking.istio.io/v1",
        "kind": "DestinationRule",
        "metadata": {
            "name": f"{server_name}-mtls-disable",
            "namespace": "istio-system",
            "labels": labels,
        },
        "spec": {
            "host": f"{server_name}.{namespace}.svc.cluster.local",
            "trafficPolicy": {
                "tls": {"mode": "DISABLE"},
            },
        },
    }

    prefix_field = "prefix" if api_group == "mcp.kuadrant.io" else "toolPrefix"

    mcp_registration = {
        "apiVersion": f"{api_group}/v1alpha1",
        "kind": "MCPServerRegistration",
        "metadata": {
            "name": server_name,
            "namespace": namespace,
            "labels": labels,
        },
        "spec": {
            prefix_field: tool_prefix,
            "targetRef": {
                "group": "gateway.networking.k8s.io",
                "kind": "HTTPRoute",
                "name": f"{server_name}-route",
            },
        },
    }

    return yaml.safe_dump_all(
        [httproute, destination_rule, mcp_registration],
        sort_keys=False,
    )
