"""Generate interactive Plotly HTML charts from captured Prometheus metrics.

Reads the raw JSON files produced by capture.py and produces one HTML chart
per metric file, styled similarly to Grafana time-series panels.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_UNIT_LABELS = {
    "cores": "CPU (cores)",
    "bytes": "Memory (bytes)",
    "bytes/s": "Throughput (bytes/s)",
    "percent": "Percentage (%)",
    "ratio": "Utilization (ratio)",
    "MiB": "Memory (MiB)",
    "watts": "Power (W)",
}


def _format_label(metric: dict[str, str]) -> str:
    """Build a readable legend label from Prometheus metric labels."""
    parts = []
    ns = metric.get("namespace")
    pod = metric.get("pod")
    instance = metric.get("instance")
    device = metric.get("device") or metric.get("gpu")

    if ns:
        parts.append(ns)
    if pod:
        parts.append(pod)
    elif instance:
        parts.append(instance)
    if device:
        parts.append(device)

    return "/".join(parts) if parts else str(metric)


def _build_figure(
    *,
    title: str,
    unit: str,
    series: list[dict[str, Any]],
    start_time: str,
    end_time: str,
) -> Any:
    """Build a Plotly figure from a list of time series."""
    try:
        import plotly.graph_objs as go
    except ImportError as e:
        raise RuntimeError(
            "plotly is required for metrics visualization. Install with: pip install plotly"
        ) from e

    fig = go.Figure()

    for s in series:
        metric_labels = s.get("metric", {})
        values = s.get("values", [])
        if not values:
            continue

        timestamps = [datetime.fromtimestamp(float(v[0]), tz=UTC) for v in values]
        data_points = [float(v[1]) for v in values]

        label = _format_label(metric_labels)
        fig.add_trace(
            go.Scatter(
                x=timestamps,
                y=data_points,
                mode="lines",
                name=label,
                hovertemplate=f"%{{x}}<br>{label}: %{{y:.3f}} {unit}<extra></extra>",
            )
        )

    y_title = _UNIT_LABELS.get(unit, unit)

    fig.update_layout(
        title=title,
        xaxis_title="Time (UTC)",
        yaxis_title=y_title,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=-0.3),
        template="plotly_dark",
        height=500,
        margin=dict(l=60, r=20, t=50, b=100),
    )

    try:
        start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        fig.add_vrect(
            x0=start_dt,
            x1=end_dt,
            fillcolor="rgba(100, 200, 100, 0.05)",
            layer="below",
            line_width=0,
        )
    except (ValueError, TypeError):
        pass

    return fig


def generate_plots(*, metrics_dir: Path, output_dir: Path | None = None) -> list[Path]:
    """
    Read raw metric JSON files and produce interactive Plotly HTML charts.

    Args:
        metrics_dir: Directory containing the raw/*.json files.
        output_dir: Where to write HTML plots. Defaults to sibling plots/ directory.

    Returns:
        List of generated HTML file paths.
    """
    raw_dir = metrics_dir
    if raw_dir.name != "raw":
        raw_dir = metrics_dir / "raw"

    if not raw_dir.is_dir():
        logger.warning("No raw metrics directory found at %s", raw_dir)
        return []

    if output_dir is None:
        output_dir = raw_dir.parent / "plots"
    output_dir.mkdir(parents=True, exist_ok=True)

    json_files = sorted(raw_dir.glob("*.json"))
    if not json_files:
        logger.info("No raw metric files to plot")
        return []

    generated: list[Path] = []

    for json_path in json_files:
        try:
            with json_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Skipping %s: %s", json_path.name, e)
            continue

        response = data.get("response", {})
        if response.get("status") != "success":
            logger.debug("Skipping %s (query status: %s)", json_path.name, response.get("status"))
            continue

        result_data = response.get("data", {})
        series = result_data.get("result", [])
        if not series:
            logger.debug("Skipping %s (no data points)", json_path.name)
            continue

        fig = _build_figure(
            title=data.get("description", data.get("query_key", json_path.stem)),
            unit=data.get("unit", ""),
            series=series,
            start_time=data.get("start", ""),
            end_time=data.get("end", ""),
        )

        html_path = output_dir / f"{json_path.stem}.html"
        fig.write_html(str(html_path), include_plotlyjs="cdn")
        generated.append(html_path)
        logger.info("  plot: %s (%d series)", html_path.name, len(series))

    logger.info("Generated %d plots in %s", len(generated), output_dir)
    return generated
