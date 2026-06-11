"""Throughput chart plot for Skeleton Caliper plugin."""

from __future__ import annotations

from pathlib import Path

import plotly.graph_objects as go

from projects.caliper.engine.model import UnifiedRunModel


class ThroughputChartPlot:
    """Generates a Plotly bar chart of throughput by scenario."""

    @staticmethod
    def generate(model: UnifiedRunModel, output_dir: Path) -> str:
        """
        Generate a throughput chart HTML report.

        Args:
            model: Unified model containing parsed test results
            output_dir: Directory to write the output file

        Returns:
            Path to the generated HTML file
        """
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

        from projects.caliper.postprocess.helpers.visualization_utils import write_full_page_html

        output_file = output_dir / "throughput_chart.html"
        write_full_page_html(fig, str(output_file), "Throughput Chart")
        return str(output_file)
