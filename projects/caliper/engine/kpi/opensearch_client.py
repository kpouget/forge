"""OpenSearch client from environment."""

from __future__ import annotations

import os
from typing import Any


def build_client() -> Any:
    """Create OpenSearch client; requires opensearch-py and env configuration."""
    try:
        from opensearchpy import OpenSearch
    except ImportError as e:
        raise RuntimeError(
            "opensearch-py is required for KPI import/export. "
            "Install with: pip install -e '.[caliper]'"
        ) from e

    hosts_env = os.environ.get("OPENSEARCH_HOSTS", "localhost:9200")
    host_list: list[dict[str, int | str]] = []
    for h in hosts_env.split(","):
        h = h.strip()
        if ":" in h:
            host, port_s = h.rsplit(":", 1)
            host_list.append({"host": host, "port": int(port_s)})
        else:
            host_list.append({"host": h, "port": 9200})

    return OpenSearch(
        hosts=host_list,
        http_compress=True,
        use_ssl=os.environ.get("OPENSEARCH_USE_SSL", "").lower() in ("1", "true", "yes"),
        verify_certs=os.environ.get("OPENSEARCH_VERIFY_CERTS", "true").lower()
        not in ("0", "false", "no"),
    )
