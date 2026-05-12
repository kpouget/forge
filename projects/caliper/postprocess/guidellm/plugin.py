"""GuideLLM Caliper PostProcessingPlugin (`projects/caliper/postprocess/guidellm`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from projects.caliper.engine.model import (
    ParseResult,
    PostProcessingPlugin,
    TestBaseNode,
    UnifiedRunModel,
)
from projects.caliper.engine.parameter_matrix import (
    create_legend_name,
    get_varying_parameters,
)

from .parsing import GuideLLMKpiHandler, GuideLLMParser


class GuideLLMPlugin(PostProcessingPlugin):
    """
    Parses GuideLLM benchmark artifacts containing ``benchmarks.json`` files.

    Extracts comprehensive LLM inference performance metrics including:
    * Request throughput and concurrency
    * Token-level latencies (TTFT, ITL, TPOT)
    * End-to-end request latency percentiles
    * Token throughput metrics
    * Request completion rates

    Visual reports (future implementation):
    * ``benchmark_summary`` — tabular view of all benchmark strategies and metrics
    * ``latency_breakdown`` — breakdown of latency components across percentiles
    * ``throughput_comparison`` — comparison of token and request throughput
    """

    def __init__(self):
        self.parser = GuideLLMParser()
        self.kpi_handler = GuideLLMKpiHandler()

    def parse(self, base_dir: Path, nodes: list[TestBaseNode]) -> ParseResult:
        """Parse test nodes using the GuideLLM parser."""
        return self.parser.parse(base_dir, nodes)

    def visualize(
        self,
        model: UnifiedRunModel,
        output_dir: Path,
        report_ids: list[str] | None,
        group_id: str | None,
        visualize_config: dict[str, Any] | None,
    ) -> list[str]:
        """Generate visualization reports for GuideLLM benchmarks."""
        output_dir.mkdir(parents=True, exist_ok=True)
        paths: list[str] = []
        wanted = frozenset(report_ids or ())

        # Filter to only GuideLLM records with benchmarks
        guidellm_records = [
            r
            for r in model.unified_result_records
            if r.run_identity.get("guidellm") and not r.metrics.get("no_benchmarks_found")
        ]

        if not guidellm_records:
            return paths

        # Generate throughput vs concurrency chart
        if "benchmark_summary" in wanted:
            path = self._generate_throughput_vs_concurrency_chart(guidellm_records, output_dir)
            if path:
                paths.append(path)

        # Generate latency breakdown chart
        if "latency_breakdown" in wanted:
            path = self._generate_latency_breakdown_chart(guidellm_records, output_dir)
            if path:
                paths.append(path)

        return paths

    def _generate_throughput_vs_concurrency_chart(
        self, records: list, output_dir: Path
    ) -> str | None:
        """Generate mean throughput vs concurrency chart."""
        try:
            import plotly.graph_objects as go

            # Get parameters that vary across all records for legend names
            varying_params = get_varying_parameters(records)

            # Group records by their distinguishing characteristics
            data_groups = {}
            for record in records:
                # Create a legend name using only varying parameters
                legend_name = create_legend_name(record, varying_params)

                concurrency = record.metrics.get(
                    "request_concurrency", record.distinguishing_labels.get("concurrency", 1)
                )
                tokens_per_second = record.metrics.get("tokens_per_second", 0)

                if legend_name not in data_groups:
                    data_groups[legend_name] = {"concurrency": [], "tokens_per_second": []}

                data_groups[legend_name]["concurrency"].append(concurrency)
                data_groups[legend_name]["tokens_per_second"].append(tokens_per_second)

            # Create figure
            fig = go.Figure()

            # Add traces for each group
            colors = ["blue", "red", "green", "purple", "orange", "brown", "pink", "gray"]
            for i, (legend_name, data) in enumerate(sorted(data_groups.items())):
                color = colors[i % len(colors)]

                fig.add_trace(
                    go.Scatter(
                        x=data["concurrency"],
                        y=data["tokens_per_second"],
                        mode="markers+lines",
                        name=legend_name,
                        marker={"color": color, "size": 8},
                        line={"color": color, "width": 2},
                    )
                )

            # Update layout
            fig.update_layout(
                title="Throughput vs Concurrency",
                xaxis_title="Concurrency Level",
                yaxis_title="Tokens per Second",
                width=800,
                height=500,
            )

            # Save the plot
            output_file = output_dir / "throughput_vs_concurrency.html"
            fig.write_html(output_file, include_plotlyjs="cdn")

            return str(output_file)

        except ImportError:
            return None
        except Exception as e:
            import logging

            logging.warning(f"Failed to generate throughput chart: {e}")
            return None

    def _generate_latency_breakdown_chart(self, records: list, output_dir: Path) -> str | None:
        """Generate latency breakdown visualization."""
        try:
            import plotly.graph_objects as go
            from plotly.subplots import make_subplots

            # Get parameters that vary across all records for legend names
            varying_params = get_varying_parameters(records)

            # Group records by their distinguishing characteristics
            data_groups = {}
            for record in records:
                # Create a legend name using only varying parameters
                legend_name = create_legend_name(record, varying_params)

                concurrency = record.metrics.get(
                    "request_concurrency", record.distinguishing_labels.get("concurrency", 1)
                )

                # Extract latency metrics (convert to ms for better readability)
                ttft_median = record.metrics.get("ttft_median", 0) * 1000  # Convert to ms
                itl_median = record.metrics.get("itl_median", 0) * 1000  # Convert to ms
                request_latency_p95 = (
                    record.metrics.get("request_latency_p95", 0) * 1000
                )  # Convert to ms

                if legend_name not in data_groups:
                    data_groups[legend_name] = {
                        "concurrency": [],
                        "ttft_median": [],
                        "itl_median": [],
                        "request_latency_p95": [],
                    }

                data_groups[legend_name]["concurrency"].append(concurrency)
                data_groups[legend_name]["ttft_median"].append(ttft_median)
                data_groups[legend_name]["itl_median"].append(itl_median)
                data_groups[legend_name]["request_latency_p95"].append(request_latency_p95)

            # Create subplots
            fig = make_subplots(
                rows=2,
                cols=2,
                subplot_titles=[
                    "Time to First Token (TTFT)",
                    "Inter-Token Latency (ITL)",
                    "Request Latency P95",
                    "Latency Comparison",
                ],
            )

            colors = ["blue", "red", "green", "purple", "orange", "brown", "pink", "gray"]

            for i, (legend_name, data) in enumerate(sorted(data_groups.items())):
                color = colors[i % len(colors)]

                # TTFT plot
                fig.add_trace(
                    go.Scatter(
                        x=data["concurrency"],
                        y=data["ttft_median"],
                        mode="markers+lines",
                        name=legend_name,
                        marker={"color": color},
                        showlegend=True,
                    ),
                    row=1,
                    col=1,
                )

                # ITL plot
                fig.add_trace(
                    go.Scatter(
                        x=data["concurrency"],
                        y=data["itl_median"],
                        mode="markers+lines",
                        name=f"{legend_name} ITL",
                        marker={"color": color},
                        showlegend=False,
                    ),
                    row=1,
                    col=2,
                )

                # Request latency P95 plot
                fig.add_trace(
                    go.Scatter(
                        x=data["concurrency"],
                        y=data["request_latency_p95"],
                        mode="markers+lines",
                        name=f"{legend_name} Req P95",
                        marker={"color": color},
                        showlegend=False,
                    ),
                    row=2,
                    col=1,
                )

                # Combined latency comparison
                fig.add_trace(
                    go.Scatter(
                        x=data["concurrency"],
                        y=data["ttft_median"],
                        mode="lines",
                        name=legend_name,
                        line={"color": color, "dash": "solid"},
                        showlegend=False,
                    ),
                    row=2,
                    col=2,
                )
                fig.add_trace(
                    go.Scatter(
                        x=data["concurrency"],
                        y=data["itl_median"],
                        mode="lines",
                        name=legend_name,
                        line={"color": color, "dash": "dash"},
                        showlegend=False,
                    ),
                    row=2,
                    col=2,
                )

            # Update layout
            fig.update_layout(title="GuideLLM Latency Analysis", height=800, showlegend=True)

            # Update axes labels
            fig.update_xaxes(title_text="Concurrency", row=2, col=1)
            fig.update_xaxes(title_text="Concurrency", row=2, col=2)
            fig.update_yaxes(title_text="Latency (ms)", row=1, col=1)
            fig.update_yaxes(title_text="Latency (ms)", row=1, col=2)
            fig.update_yaxes(title_text="Latency (ms)", row=2, col=1)
            fig.update_yaxes(title_text="Latency (ms)", row=2, col=2)

            # Save the plot
            output_file = output_dir / "latency_breakdown.html"
            fig.write_html(output_file, include_plotlyjs="cdn")

            return str(output_file)

        except ImportError:
            return None
        except Exception as e:
            import logging

            logging.warning(f"Failed to generate latency breakdown chart: {e}")
            return None

    def kpi_catalog(self) -> list[dict[str, Any]]:
        """Return the GuideLLM KPI catalog."""
        return self.kpi_handler.get_catalog()

    def compute_kpis(self, model: UnifiedRunModel) -> list[dict[str, Any]]:
        """Compute KPI values from the unified model."""
        return self.kpi_handler.compute_kpis(model)

    def build_ai_eval_payload(self, model: UnifiedRunModel) -> dict[str, Any]:
        """Build AI evaluation payload from the unified model."""
        # Extract GuideLLM-specific metrics for AI evaluation
        benchmarks = []
        for r in model.unified_result_records:
            if r.run_identity.get("guidellm") and not r.metrics.get("no_benchmarks_found"):
                strategy_info = {
                    "strategy": r.distinguishing_labels.get("strategy", "unknown"),
                    "concurrency": r.distinguishing_labels.get("concurrency", 1.0),
                    "request_rate": r.metrics.get("request_rate", 0.0),
                    "tokens_per_second": r.metrics.get("tokens_per_second", 0.0),
                    "ttft_median": r.metrics.get("ttft_median", 0.0),
                    "itl_median": r.metrics.get("itl_median", 0.0),
                    "request_latency_p95": r.metrics.get("request_latency_p95", 0.0),
                }
                benchmarks.append(strategy_info)

        return {
            "schema_version": "1",
            "run_id": model.base_directory,
            "metrics": {
                "record_count": len(model.unified_result_records),
                "benchmark_count": len(benchmarks),
                "strategies": [b["strategy"] for b in benchmarks],
                "max_request_rate": max([b["request_rate"] for b in benchmarks], default=0.0),
                "max_tokens_per_second": max(
                    [b["tokens_per_second"] for b in benchmarks], default=0.0
                ),
                "min_ttft_median": min(
                    [b["ttft_median"] for b in benchmarks if b["ttft_median"] > 0], default=0.0
                ),
            },
            "benchmarks": benchmarks,
        }


def get_plugin() -> PostProcessingPlugin:
    """Return the GuideLLM plugin instance."""
    return GuideLLMPlugin()
