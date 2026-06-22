"""Pydantic configuration model for Prometheus metrics capture."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class MetricsCaptureConfig(BaseModel):
    """
    Configuration for resource utilization metrics capture.

    Embed under a ``metrics:`` key in a project's config.d/ YAML::

        metrics:
          enabled: true
          namespaces: [mcp-gw-bench, mcp-system, gateway-system]
          step_seconds: 15
          query_keys: [cpu_usage, memory_usage, network_rx_bytes]

    When ``query_keys`` is empty (the default), every query defined in
    ``queries.yaml`` is executed.
    """

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    namespaces: list[str] = Field(default_factory=list)
    step_seconds: int = Field(15, ge=1, le=300)
    query_keys: list[str] = Field(default_factory=list)
