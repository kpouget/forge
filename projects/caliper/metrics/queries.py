"""PromQL query definitions for resource utilization metrics.

Each query is parametrized by a pipe-separated namespace list (e.g. "ns1|ns2|ns3").
Queries are grouped into categories that can be selectively enabled via config.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QuerySpec:
    """A named PromQL query with display metadata."""

    key: str
    category: str
    promql_template: str
    unit: str
    description: str


def _ns_regex(namespaces: list[str]) -> str:
    return "|".join(namespaces)


def get_pod_queries(namespaces: list[str]) -> dict[str, list[QuerySpec]]:
    """Return pod-level query specs grouped by category, parametrized by namespaces."""
    ns = _ns_regex(namespaces)

    return {
        "cpu": [
            QuerySpec(
                key="cpu_usage",
                category="cpu",
                promql_template=(
                    f"sum(rate(container_cpu_usage_seconds_total"
                    f'{{namespace=~"{ns}",container!="POD",container!=""}}[1m])) '
                    f"by (namespace, pod)"
                ),
                unit="cores",
                description="CPU usage per pod (cores)",
            ),
        ],
        "memory": [
            QuerySpec(
                key="memory_usage",
                category="memory",
                promql_template=(
                    f"sum(container_memory_working_set_bytes"
                    f'{{namespace=~"{ns}",container!="POD",container!=""}}) '
                    f"by (namespace, pod)"
                ),
                unit="bytes",
                description="Memory working set per pod (bytes)",
            ),
        ],
        "network": [
            QuerySpec(
                key="network_rx",
                category="network",
                promql_template=(
                    f"sum(rate(container_network_receive_bytes_total"
                    f'{{namespace=~"{ns}"}}[1m])) by (namespace, pod)'
                ),
                unit="bytes/s",
                description="Network receive rate per pod (bytes/s)",
            ),
            QuerySpec(
                key="network_tx",
                category="network",
                promql_template=(
                    f"sum(rate(container_network_transmit_bytes_total"
                    f'{{namespace=~"{ns}"}}[1m])) by (namespace, pod)'
                ),
                unit="bytes/s",
                description="Network transmit rate per pod (bytes/s)",
            ),
        ],
        "throttling": [
            QuerySpec(
                key="cpu_throttling",
                category="throttling",
                promql_template=(
                    f"sum(rate(container_cpu_cfs_throttled_periods_total"
                    f'{{namespace=~"{ns}",container!="POD",container!=""}}[5m])) by (namespace, pod) '
                    f"/ sum(rate(container_cpu_cfs_periods_total"
                    f'{{namespace=~"{ns}",container!="POD",container!=""}}[5m])) by (namespace, pod) '
                    f"* 100"
                ),
                unit="percent",
                description="CPU throttle percentage per pod",
            ),
        ],
    }


def get_node_queries() -> dict[str, list[QuerySpec]]:
    """Return cluster-wide node-level query specs."""
    return {
        "node_cpu": [
            QuerySpec(
                key="node_cpu",
                category="node_cpu",
                promql_template="instance:node_cpu_utilisation:rate5m",
                unit="ratio",
                description="Node CPU utilization (0-1 ratio)",
            ),
        ],
        "node_memory": [
            QuerySpec(
                key="node_memory",
                category="node_memory",
                promql_template="instance:node_memory_utilisation:ratio",
                unit="ratio",
                description="Node memory utilization (0-1 ratio)",
            ),
        ],
        "node_network": [
            QuerySpec(
                key="node_network_rx",
                category="node_network",
                promql_template="instance:node_network_receive_bytes_excluding_lo:rate5m",
                unit="bytes/s",
                description="Node network receive rate (bytes/s)",
            ),
        ],
    }


def get_gpu_queries() -> dict[str, list[QuerySpec]]:
    """Return GPU (DCGM) query specs. Only useful if DCGM exporter is deployed."""
    return {
        "gpu": [
            QuerySpec(
                key="gpu_utilization",
                category="gpu",
                promql_template="DCGM_FI_DEV_GPU_UTIL",
                unit="percent",
                description="GPU compute utilization (%)",
            ),
            QuerySpec(
                key="gpu_memory_used",
                category="gpu",
                promql_template="DCGM_FI_DEV_FB_USED",
                unit="MiB",
                description="GPU framebuffer memory used (MiB)",
            ),
            QuerySpec(
                key="gpu_power",
                category="gpu",
                promql_template="DCGM_FI_DEV_POWER_USAGE",
                unit="watts",
                description="GPU power draw (watts)",
            ),
        ],
    }


def resolve_queries(
    categories: list[str],
    namespaces: list[str],
    include_gpu: bool = False,
) -> list[QuerySpec]:
    """Resolve category names to a flat list of QuerySpec objects."""
    pod_queries = get_pod_queries(namespaces)
    node_queries = get_node_queries()
    gpu_queries = get_gpu_queries() if include_gpu else {}

    all_available = {**pod_queries, **node_queries, **gpu_queries}

    specs: list[QuerySpec] = []
    for category in categories:
        if category in all_available:
            specs.extend(all_available[category])

    return specs
