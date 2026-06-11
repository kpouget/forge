"""Summary table plot for Skeleton Caliper plugin."""

from __future__ import annotations

import html as html_lib
from pathlib import Path

from projects.caliper.engine.model import UnifiedRunModel


class SummaryTablePlot:
    """Generates an HTML summary table of test results."""

    @staticmethod
    def generate(model: UnifiedRunModel, output_dir: Path) -> str:
        """
        Generate a summary table HTML report.

        Args:
            model: Unified model containing parsed test results
            output_dir: Directory to write the output file

        Returns:
            Path to the generated HTML file
        """
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

        output_file = output_dir / "summary_table.html"
        output_file.write_text(table_html, encoding="utf-8")
        return str(output_file)
