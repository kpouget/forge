"""Load PromQL query definitions from the YAML catalog.

Queries are defined in ``queries.yaml`` (sibling file).  Each entry has a
unique key, a PromQL template with an ``{ns}`` placeholder for the namespace
regex, a unit, and a human-readable description.

Projects select queries by key in their config.  When no keys are specified
the full catalog is returned.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_QUERIES_YAML = Path(__file__).with_name("queries.yaml")


@dataclass(frozen=True)
class QuerySpec:
    """A named PromQL query with display metadata."""

    key: str
    category: str
    promql: str
    unit: str
    description: str


def _load_catalog() -> dict[str, dict[str, Any]]:
    """Read and cache the raw YAML catalog."""
    with _QUERIES_YAML.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data.get("queries", {})


def load_queries(
    *,
    namespaces: list[str],
    keys: list[str] | None = None,
) -> list[QuerySpec]:
    """Resolve query keys to a list of ``QuerySpec`` objects.

    Args:
        namespaces: Kubernetes namespaces.  Joined with ``|`` and
            substituted into the ``{ns}`` placeholder of each PromQL template.
        keys: Query keys to include.  ``None`` or empty list means *all*.

    Returns:
        List of ``QuerySpec`` with the ``{ns}`` placeholder resolved.
    """
    catalog = _load_catalog()
    ns_regex = "|".join(namespaces)

    if keys:
        missing = [k for k in keys if k not in catalog]
        if missing:
            logger.warning("Unknown query keys (skipped): %s", missing)
        selected = {k: catalog[k] for k in keys if k in catalog}
    else:
        selected = catalog

    specs: list[QuerySpec] = []
    for key, entry in selected.items():
        promql = entry["promql"].replace("{ns}", ns_regex)
        specs.append(
            QuerySpec(
                key=key,
                category=entry["category"],
                promql=promql,
                unit=entry["unit"],
                description=entry["description"],
            )
        )

    return specs
