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
from pathlib import Path

import yaml

from projects.agentic_tools.mcp.toolbox.deploy_mock_servers.main import SCALE_OUT_LABEL
from projects.core.dsl import entrypoint, execute_tasks, retry, task
from projects.core.dsl.utils.k8s import oc

logger = logging.getLogger(__name__)

DEFAULT_API_GROUP = "mcp.kuadrant.io"


@entrypoint
def run(
    *,
    namespace: str,
    count: int,
    name_prefix: str = "mock-server",
    gateway_namespace: str = "gateway-system",
    gateway_name: str = "mcp-gateway",
    api_group: str = DEFAULT_API_GROUP,
) -> int:
    """Apply MCP Gateway infrastructure and wait for registrations."""
    execute_tasks(locals())
    return 0


@task
def apply_manifests(args, ctx):
    """Generate and apply HTTPRoute + DestinationRule + MCPServerRegistration."""
    logger.info(
        "Applying infrastructure for %d server(s) (api_group=%s)...",
        args.count,
        args.api_group,
    )

    all_manifests = []
    for i in range(1, args.count + 1):
        server_name = f"{args.name_prefix}-{i}"
        hostname = f"server{i}.mcp.local"
        tool_prefix = f"server{i}_"

        manifest = _generate_infrastructure_manifest(
            server_name=server_name,
            hostname=hostname,
            tool_prefix=tool_prefix,
            namespace=args.namespace,
            gateway_namespace=args.gateway_namespace,
            gateway_name=args.gateway_name,
            api_group=args.api_group,
        )
        all_manifests.append(manifest)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tmp:
        tmp.write("---\n".join(all_manifests))
        tmp_path = tmp.name
    oc("apply", "-f", tmp_path)
    Path(tmp_path).unlink(missing_ok=True)

    return f"Infrastructure applied for {args.count} server(s)"


@retry(attempts=60, delay=5, backoff=1.0)
@task
def wait_for_registrations(args, ctx):
    """Wait until all N MCPServerRegistrations report Ready."""
    ready_count = _count_ready_registrations(namespace=args.namespace, api_group=args.api_group)

    if ready_count >= args.count:
        return f"All {args.count} registrations are Ready"

    return (False, f"registrations ready: {ready_count}/{args.count}")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


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
    labels = {
        "experiment": "scale-out",
        "forge.openshift.io/project": "mcp_gateway",
    }

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
