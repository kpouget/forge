"""AI evaluation payload builder for GuideLLM benchmarks."""

from __future__ import annotations

from typing import Any

from projects.caliper.engine.model import UnifiedRunModel


class GuideLLMAIEvaluator:
    """Handles AI evaluation payload generation for GuideLLM benchmark results."""

    def __init__(self):
        """Initialize the AI evaluator."""
        self.schema_version = "1"

    def build_payload(self, model: UnifiedRunModel) -> dict[str, Any]:
        """Build AI evaluation payload from the unified model.

        Args:
            model: Unified model containing benchmark results

        Returns:
            Dictionary containing structured AI evaluation data with:
            - schema_version: Version of the payload format
            - run_id: Identifier for the benchmark run
            - metrics: Aggregated metrics across all benchmarks
            - benchmarks: Individual benchmark strategy details
        """
        # Extract GuideLLM-specific metrics for AI evaluation
        benchmarks = []
        for record in model.unified_result_records:
            if record.run_identity.get("guidellm") and not record.metrics.get(
                "no_benchmarks_found"
            ):
                strategy_info = {
                    "strategy": record.distinguishing_labels.get("strategy", "unknown"),
                    "concurrency": record.distinguishing_labels.get("concurrency", 1.0),
                    "request_rate": record.metrics.get("request_rate", 0.0),
                    "tokens_per_second": record.metrics.get("tokens_per_second", 0.0),
                    "ttft_median": record.metrics.get("ttft_median", 0.0),
                    "itl_median": record.metrics.get("itl_median", 0.0),
                    "request_latency_p95": record.metrics.get("request_latency_p95", 0.0),
                }
                benchmarks.append(strategy_info)

        # Compute aggregated metrics
        metrics = self._compute_aggregated_metrics(model.unified_result_records, benchmarks)

        return {
            "schema_version": self.schema_version,
            "run_id": str(model.base_directory),
            "metrics": metrics,
            "benchmarks": benchmarks,
        }

    def _compute_aggregated_metrics(
        self, all_records: list, benchmarks: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Compute aggregated metrics across all benchmark strategies.

        Args:
            all_records: All unified result records
            benchmarks: Extracted benchmark strategy data

        Returns:
            Dictionary with aggregated metrics
        """
        return {
            "record_count": len(all_records),
            "benchmark_count": len(benchmarks),
            "strategies": [b["strategy"] for b in benchmarks],
            "max_request_rate": max([b["request_rate"] for b in benchmarks], default=0.0),
            "max_tokens_per_second": max([b["tokens_per_second"] for b in benchmarks], default=0.0),
            "min_ttft_median": min(
                [b["ttft_median"] for b in benchmarks if b["ttft_median"] > 0], default=0.0
            ),
        }

    def get_schema_version(self) -> str:
        """Get the current schema version for AI evaluation payloads."""
        return self.schema_version

    def validate_payload(self, payload: dict[str, Any]) -> bool:
        """Validate that a payload has the expected structure.

        Args:
            payload: AI evaluation payload to validate

        Returns:
            True if payload structure is valid, False otherwise
        """
        required_keys = {"schema_version", "run_id", "metrics", "benchmarks"}
        if not all(key in payload for key in required_keys):
            return False

        required_metric_keys = {
            "record_count",
            "benchmark_count",
            "strategies",
            "max_request_rate",
            "max_tokens_per_second",
            "min_ttft_median",
        }
        if not all(key in payload["metrics"] for key in required_metric_keys):
            return False

        return True
