"""Sample Caliper PostProcessingPlugin for Skeleton (`projects/skeleton/postprocess/default`)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from projects.caliper.engine.model import (
    ParseResult,
    PostProcessingPlugin,
    TestBaseNode,
    UnifiedResultRecord,
    UnifiedRunModel,
)


def _labels_from_node(node: TestBaseNode) -> dict[str, Any]:
    raw = node.labels
    inner = raw.get("labels")
    if isinstance(inner, dict):
        return dict(inner)
    if isinstance(raw, dict):
        return dict(raw)
    return {"facet": "default"}


class SkeletonDefaultPlugin(PostProcessingPlugin):
    """
    Parses per-test directories containing ``metrics.json`` (simple numeric mapping).

    Visual reports (Plotly HTML):

    * ``summary_table`` — tabular view of scenarios and metrics.
    * ``throughput_chart`` — bar chart of ``throughput`` when present.
    """

    def parse(self, base_dir: Path, nodes: list[TestBaseNode]) -> ParseResult:
        records: list[UnifiedResultRecord] = []
        warnings: list[str] = []
        for node in nodes:
            metrics: dict[str, Any] = {}
            for p in node.artifact_paths:
                if p.name != "metrics.json":
                    continue
                try:
                    metrics = json.loads(p.read_text(encoding="utf-8"))
                    if not isinstance(metrics, dict):
                        warnings.append(f"{p}: metrics.json must be a JSON object")
                        metrics = {}
                except json.JSONDecodeError as e:
                    warnings.append(f"Malformed JSON {p}: {e}")
                    metrics = {"_parse_error": True}
                break
            labels = _labels_from_node(node)
            records.append(
                UnifiedResultRecord(
                    test_base_path=str(node.directory.relative_to(base_dir.resolve())),
                    distinguishing_labels=labels,
                    metrics=dict(metrics) if metrics else {"throughput": 0.0},
                    run_identity={"skeleton_sample": True},
                    parse_notes=[],
                )
            )
        return ParseResult(records=records, warnings=warnings)

    def visualize(
        self,
        model: UnifiedRunModel,
        output_dir: Path,
        report_ids: list[str] | None,
        group_id: str | None,
        visualize_config: dict[str, Any] | None,
    ) -> list[str]:
        import html as html_lib

        import plotly.graph_objects as go

        output_dir.mkdir(parents=True, exist_ok=True)
        paths: list[str] = []
        wanted = frozenset(report_ids or ())

        if "summary_table" in wanted:
            rows = []
            for r in model.unified_result_records:
                scenario = r.distinguishing_labels.get("scenario", "")
                tp = r.metrics.get("throughput", "")
                lat = r.metrics.get("latency_ms", "")
                rows.append(
                    "<tr>"
                    f"<td>{html_lib.escape(str(r.test_base_path))}</td>"
                    f"<td>{html_lib.escape(str(scenario))}</td>"
                    f"<td>{html_lib.escape(str(tp))}</td>"
                    f"<td>{html_lib.escape(str(lat))}</td>"
                    "</tr>"
                )
            table_html = (
                "<!DOCTYPE html><html><head><meta charset='utf-8'/>"
                "<title>Skeleton sample — summary</title>"
                "<style>body{font-family:system-ui;margin:1.5rem}"
                "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:.4rem .6rem}"
                "</style></head><body>"
                "<h1>Skeleton default plugin — summary</h1>"
                "<table><thead><tr>"
                "<th>test_base_path</th><th>scenario</th><th>throughput</th><th>latency_ms</th>"
                "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></body></html>"
            )
            out = output_dir / "summary_table.html"
            out.write_text(table_html, encoding="utf-8")
            paths.append(str(out))

        if "throughput_chart" in wanted:
            xs: list[str] = []
            ys: list[float] = []
            for r in model.unified_result_records:
                label = str(r.distinguishing_labels.get("scenario") or r.test_base_path)
                raw = r.metrics.get("throughput", 0)
                try:
                    y = float(raw)
                except (TypeError, ValueError):
                    y = 0.0
                xs.append(label)
                ys.append(y)
            fig = go.Figure(data=[go.Bar(x=xs, y=ys)])
            fig.update_layout(
                title="Throughput by scenario",
                xaxis_title="Scenario",
                yaxis_title="Throughput",
            )
            out = output_dir / "throughput_chart.html"
            fig.write_html(out, include_plotlyjs="cdn")
            paths.append(str(out))

        return paths

    def kpi_catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "kpi_id": "skeleton_throughput_rps",
                "name": "Skeleton throughput",
                "unit": "req/s",
                "higher_is_better": True,
            },
            {
                "kpi_id": "skeleton_latency_ms",
                "name": "Skeleton latency",
                "unit": "ms",
                "higher_is_better": False,
            },
        ]

    def compute_kpis(self, model: UnifiedRunModel) -> list[dict[str, Any]]:
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        out: list[dict[str, Any]] = []
        for r in model.unified_result_records:
            tp_raw = r.metrics.get("throughput", 0)
            lat_raw = r.metrics.get("latency_ms", 0)
            try:
                tp = float(tp_raw)
            except (TypeError, ValueError):
                tp = 0.0
            try:
                lat = float(lat_raw)
            except (TypeError, ValueError):
                lat = 0.0
            base_labels = {
                **r.distinguishing_labels,
            }
            out.append(
                {
                    "schema_version": "1",
                    "kpi_id": "skeleton_throughput_rps",
                    "value": tp,
                    "unit": "req/s",
                    "run_id": r.test_base_path,
                    "timestamp": ts,
                    "labels": {**base_labels, "higher_is_better": True},
                    "source": {
                        "test_base_path": r.test_base_path,
                        "plugin_module": model.plugin_module,
                    },
                }
            )
            out.append(
                {
                    "schema_version": "1",
                    "kpi_id": "skeleton_latency_ms",
                    "value": lat,
                    "unit": "ms",
                    "run_id": r.test_base_path,
                    "timestamp": ts,
                    "labels": {**base_labels, "higher_is_better": False},
                    "source": {
                        "test_base_path": r.test_base_path,
                        "plugin_module": model.plugin_module,
                    },
                }
            )
        return out

    def build_ai_eval_payload(self, model: UnifiedRunModel) -> dict[str, Any]:
        return {
            "schema_version": "1",
            "run_id": model.base_directory,
            "metrics": {
                "record_count": len(model.unified_result_records),
                "scenarios": [
                    str(r.distinguishing_labels.get("scenario", r.test_base_path))
                    for r in model.unified_result_records
                ],
            },
            "optional": {},
        }


def get_plugin() -> PostProcessingPlugin:
    return SkeletonDefaultPlugin()
