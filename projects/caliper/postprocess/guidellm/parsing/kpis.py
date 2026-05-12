"""KPI definitions and computation for GuideLLM Caliper plugin."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from projects.caliper.engine.model import UnifiedRunModel


class GuideLLMKpiHandler:
    """Handles KPI catalog and computation for GuideLLM benchmarks."""

    @staticmethod
    def get_catalog() -> list[dict[str, Any]]:
        """
        Return the KPI catalog for GuideLLM metrics.

        Returns:
            List of KPI definitions
        """
        return [
            # Throughput KPIs
            {
                "kpi_id": "guidellm_request_rate",
                "name": "Request Rate",
                "unit": "req/s",
                "higher_is_better": True,
            },
            {
                "kpi_id": "guidellm_tokens_per_second",
                "name": "Total Token Throughput",
                "unit": "tokens/s",
                "higher_is_better": True,
            },
            {
                "kpi_id": "guidellm_input_tokens_per_second",
                "name": "Input Token Throughput",
                "unit": "tokens/s",
                "higher_is_better": True,
            },
            {
                "kpi_id": "guidellm_output_tokens_per_second",
                "name": "Output Token Throughput",
                "unit": "tokens/s",
                "higher_is_better": True,
            },
            # Latency KPIs
            {
                "kpi_id": "guidellm_ttft_median",
                "name": "Time to First Token (Median)",
                "unit": "s",
                "higher_is_better": False,
            },
            {
                "kpi_id": "guidellm_ttft_p95",
                "name": "Time to First Token (P95)",
                "unit": "s",
                "higher_is_better": False,
            },
            {
                "kpi_id": "guidellm_itl_median",
                "name": "Inter Token Latency (Median)",
                "unit": "s",
                "higher_is_better": False,
            },
            {
                "kpi_id": "guidellm_tpot_median",
                "name": "Time Per Output Token (Median)",
                "unit": "s",
                "higher_is_better": False,
            },
            {
                "kpi_id": "guidellm_request_latency_median",
                "name": "End-to-End Request Latency (Median)",
                "unit": "s",
                "higher_is_better": False,
            },
            {
                "kpi_id": "guidellm_request_latency_p95",
                "name": "End-to-End Request Latency (P95)",
                "unit": "s",
                "higher_is_better": False,
            },
            # Token efficiency KPIs
            {
                "kpi_id": "guidellm_input_tokens_per_request",
                "name": "Input Tokens per Request",
                "unit": "tokens",
                "higher_is_better": False,  # Generally want efficiency
            },
            {
                "kpi_id": "guidellm_output_tokens_per_request",
                "name": "Output Tokens per Request",
                "unit": "tokens",
                "higher_is_better": False,  # Generally want conciseness
            },
        ]

    @staticmethod
    def compute_kpis(model: UnifiedRunModel) -> list[dict[str, Any]]:
        """
        Compute KPI values from the unified model.

        Args:
            model: Unified model containing parsed test results

        Returns:
            List of KPI records
        """
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        out: list[dict[str, Any]] = []

        for r in model.unified_result_records:
            # Skip records without GuideLLM data
            if not r.run_identity.get("guidellm"):
                continue

            # Skip if no benchmarks found
            if r.metrics.get("no_benchmarks_found"):
                continue

            base_labels = {**r.distinguishing_labels}

            # Define KPI mappings
            kpi_mappings = [
                ("guidellm_request_rate", "request_rate", "req/s", True),
                ("guidellm_tokens_per_second", "tokens_per_second", "tokens/s", True),
                ("guidellm_input_tokens_per_second", "input_tokens_per_second", "tokens/s", True),
                ("guidellm_output_tokens_per_second", "output_tokens_per_second", "tokens/s", True),
                ("guidellm_ttft_median", "ttft_median", "s", False),
                ("guidellm_ttft_p95", "ttft_p95", "s", False),
                ("guidellm_itl_median", "itl_median", "s", False),
                ("guidellm_tpot_median", "tpot_median", "s", False),
                ("guidellm_request_latency_median", "request_latency_median", "s", False),
                ("guidellm_request_latency_p95", "request_latency_p95", "s", False),
                ("guidellm_input_tokens_per_request", "input_tokens_per_request", "tokens", False),
                (
                    "guidellm_output_tokens_per_request",
                    "output_tokens_per_request",
                    "tokens",
                    False,
                ),
            ]

            # Extract and create KPI records
            for kpi_id, metric_key, unit, higher_is_better in kpi_mappings:
                raw_value = r.metrics.get(metric_key, 0)

                # Convert to float
                try:
                    value = float(raw_value)
                except (TypeError, ValueError):
                    value = 0.0

                out.append(
                    {
                        "schema_version": "1",
                        "kpi_id": kpi_id,
                        "value": value,
                        "unit": unit,
                        "run_id": r.test_base_path,
                        "timestamp": ts,
                        "labels": {**base_labels, "higher_is_better": higher_is_better},
                        "source": {
                            "test_base_path": r.test_base_path,
                            "plugin_module": model.plugin_module,
                        },
                    }
                )

        return out
