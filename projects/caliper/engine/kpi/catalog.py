"""Resolve KPI catalog from plugin."""

from __future__ import annotations

from projects.caliper.engine.model import PostProcessingPlugin


def get_catalog(plugin: PostProcessingPlugin) -> list[dict[str, object]]:
    return plugin.kpi_catalog()
