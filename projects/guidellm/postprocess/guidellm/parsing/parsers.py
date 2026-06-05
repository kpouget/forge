"""GuideLLM benchmark parsers for Caliper plugin."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from projects.caliper.engine.model import (
    ParseResult,
    TestBaseNode,
    UnifiedResultRecord,
)

from .models import GuideLLMBenchmark, GuideLLMConfiguration


def _labels_from_node(node: TestBaseNode) -> dict[str, Any]:
    """Extract labels from a test node."""
    raw = node.labels
    inner = raw.get("labels")
    if isinstance(inner, dict):
        return dict(inner)
    if isinstance(raw, dict):
        return dict(raw)
    return {"facet": "default"}


class GuideLLMParser:
    """Parser for GuideLLM benchmark JSON artifacts."""

    def parse_benchmarks_json(
        self, file_path: Path
    ) -> tuple[list[GuideLLMBenchmark], GuideLLMConfiguration | None, list[str]]:
        """
        Parse a GuideLLM benchmarks.json file.

        Returns:
            Tuple of (benchmarks list, configuration, warnings list)
        """
        warnings: list[str] = []
        benchmarks: list[GuideLLMBenchmark] = []
        configuration: GuideLLMConfiguration | None = None

        try:
            json_data = json.loads(file_path.read_text(encoding="utf-8"))
            if not isinstance(json_data, dict):
                warnings.append(f"{file_path}: benchmarks.json must be a JSON object")
                return [], None, warnings

            # Parse configuration from top-level fields
            args = json_data.get("args")
            metadata = json_data.get("metadata")
            if args or metadata:
                configuration = GuideLLMConfiguration(args=args, metadata=metadata)

            # Parse each benchmark in the JSON
            for benchmark_data in json_data.get("benchmarks", []):
                try:
                    benchmark = self._parse_single_benchmark(benchmark_data)
                    benchmarks.append(benchmark)
                except Exception as e:
                    warnings.append(f"Failed to parse benchmark in {file_path}: {e}")
                    logging.warning(f"Failed to parse benchmark data: {e}")
                    continue

            logging.info(f"Parsed {len(benchmarks)} GuideLLM benchmarks from {file_path}")
            return benchmarks, configuration, warnings

        except json.JSONDecodeError as e:
            warnings.append(f"Malformed JSON {file_path}: {e}")
            return [], None, warnings
        except Exception as e:
            warnings.append(f"Failed to parse GuideLLM JSON {file_path}: {e}")
            return [], None, warnings

    def _parse_single_benchmark(self, benchmark_data: dict[str, Any]) -> GuideLLMBenchmark:
        """Parse a single benchmark entry from the JSON data."""
        # Extract strategy and concurrency info with fallback logic
        scheduler = benchmark_data.get("scheduler", {})
        config = benchmark_data.get("config", {})
        strategy_info = config.get("strategy", {})
        strategy = strategy_info.get("type_", "unknown")

        # Extract concurrency (streams) with multiple fallback paths
        concurrency = self._extract_concurrency(strategy_info, scheduler)

        # Extract timing info
        state = scheduler.get("state", {})
        start_time = state.get("start_time", 0)
        end_time = state.get("end_time", 0)
        duration = end_time - start_time if end_time > start_time else 60.0

        # Extract metrics
        metrics = benchmark_data.get("metrics", {})

        # Helper function to safely extract metric values
        def get_metric_value(
            metric_name: str, stat_type: str = "median", default: float = 0.0
        ) -> float:
            metric_data = metrics.get(metric_name, {}).get("successful", {})
            if stat_type in ["p95", "p90", "p75", "p50", "p25", "p10"]:
                percentiles = metric_data.get("percentiles", {})
                return float(percentiles.get(stat_type, default))
            else:
                return float(metric_data.get(stat_type, default))

        # Extract latency metrics (convert ms to seconds for consistency)
        request_latency_median = get_metric_value("request_latency", "median") / 1000.0
        request_latency_p95 = get_metric_value("request_latency", "p95") / 1000.0

        # Extract TTFT percentiles
        ttft_median = get_metric_value("time_to_first_token_ms", "median") / 1000.0
        ttft_p10 = get_metric_value("time_to_first_token_ms", "p10") / 1000.0
        ttft_p25 = get_metric_value("time_to_first_token_ms", "p25") / 1000.0
        ttft_p50 = get_metric_value("time_to_first_token_ms", "p50") / 1000.0
        ttft_p75 = get_metric_value("time_to_first_token_ms", "p75") / 1000.0
        ttft_p90 = get_metric_value("time_to_first_token_ms", "p90") / 1000.0
        ttft_p95 = get_metric_value("time_to_first_token_ms", "p95") / 1000.0

        # Extract ITL percentiles
        itl_median = get_metric_value("inter_token_latency_ms", "median") / 1000.0
        itl_p10 = get_metric_value("inter_token_latency_ms", "p10") / 1000.0
        itl_p25 = get_metric_value("inter_token_latency_ms", "p25") / 1000.0
        itl_p50 = get_metric_value("inter_token_latency_ms", "p50") / 1000.0
        itl_p75 = get_metric_value("inter_token_latency_ms", "p75") / 1000.0
        itl_p90 = get_metric_value("inter_token_latency_ms", "p90") / 1000.0
        itl_p95 = get_metric_value("inter_token_latency_ms", "p95") / 1000.0

        # Extract TPOT percentiles
        tpot_median = get_metric_value("time_per_output_token_ms", "median") / 1000.0
        tpot_p95 = get_metric_value("time_per_output_token_ms", "p95") / 1000.0

        # Extract throughput metrics
        request_rate = get_metric_value("requests_per_second", "mean")
        input_tokens_per_second = get_metric_value("input_tokens_per_second", "mean")
        output_tokens_per_second = get_metric_value("output_tokens_per_second", "mean")
        total_tokens_per_second = input_tokens_per_second + output_tokens_per_second

        # Extract output token percentiles
        output_tokens_per_second_p10 = get_metric_value("output_tokens_per_second", "p10")
        output_tokens_per_second_p25 = get_metric_value("output_tokens_per_second", "p25")
        output_tokens_per_second_p50 = get_metric_value("output_tokens_per_second", "p50")
        output_tokens_per_second_p75 = get_metric_value("output_tokens_per_second", "p75")
        output_tokens_per_second_p90 = get_metric_value("output_tokens_per_second", "p90")

        # Calculate requests completed and tokens per request
        completed_requests = int(request_rate * duration) if request_rate > 0 else 0
        input_tokens_per_request = (
            (input_tokens_per_second / request_rate) if request_rate > 0 else 0.0
        )
        output_tokens_per_request = (
            (output_tokens_per_second / request_rate) if request_rate > 0 else 0.0
        )
        total_tokens_per_request = (
            (total_tokens_per_second / request_rate) if request_rate > 0 else 0.0
        )

        # Create GuideLLMBenchmark object
        return GuideLLMBenchmark(
            strategy=strategy,
            duration=duration,
            warmup_time=0.0,  # Not available in JSON format
            cooldown_time=0.0,  # Not available in JSON format
            # Request metrics
            request_rate=request_rate,
            request_concurrency=concurrency,
            completed_requests=completed_requests,
            failed_requests=0,  # Could extract from unsuccessful metrics if needed
            # Token metrics per request
            input_tokens_per_request=input_tokens_per_request,
            output_tokens_per_request=output_tokens_per_request,
            total_tokens_per_request=total_tokens_per_request,
            # Latency metrics (already in seconds)
            request_latency_median=request_latency_median,
            request_latency_p95=request_latency_p95,
            ttft_median=ttft_median,
            ttft_p10=ttft_p10,
            ttft_p25=ttft_p25,
            ttft_p50=ttft_p50,
            ttft_p75=ttft_p75,
            ttft_p90=ttft_p90,
            ttft_p95=ttft_p95,
            itl_median=itl_median,
            itl_p10=itl_p10,
            itl_p25=itl_p25,
            itl_p50=itl_p50,
            itl_p75=itl_p75,
            itl_p90=itl_p90,
            itl_p95=itl_p95,
            tpot_median=tpot_median,
            tpot_p95=tpot_p95,
            # Throughput metrics
            tokens_per_second=total_tokens_per_second,
            input_tokens_per_second=input_tokens_per_second,
            output_tokens_per_second=output_tokens_per_second,
            # Output token percentiles
            output_tokens_per_second_p10=output_tokens_per_second_p10,
            output_tokens_per_second_p25=output_tokens_per_second_p25,
            output_tokens_per_second_p50=output_tokens_per_second_p50,
            output_tokens_per_second_p75=output_tokens_per_second_p75,
            output_tokens_per_second_p90=output_tokens_per_second_p90,
        )

    def _extract_concurrency(
        self, strategy_info: dict[str, Any], scheduler: dict[str, Any]
    ) -> float:
        """Extract concurrency (streams) from strategy or scheduler info."""
        # Try multiple paths for concurrency extraction
        try:
            # First try: config.strategy.streams
            concurrency = float(strategy_info.get("streams", 0))
            if concurrency > 0:
                return concurrency
        except (ValueError, TypeError):
            pass

        try:
            # Second try: scheduler.strategy.streams
            sched_strategy = scheduler.get("strategy", {})
            streams = sched_strategy.get("streams")
            if streams and streams > 0:
                return float(streams)
        except (ValueError, TypeError):
            pass

        logging.warning(
            "Could not find concurrency 'streams' for benchmark. Using default value 1.0"
        )
        return 1.0

    def parse(self, base_dir: Path, nodes: list[TestBaseNode]) -> ParseResult:
        """
        Parse test nodes containing GuideLLM benchmarks.json files.

        Args:
            base_dir: Base directory for the test run
            nodes: List of test nodes to parse

        Returns:
            ParseResult with unified records and warnings
        """
        records: list[UnifiedResultRecord] = []
        warnings: list[str] = []

        for node in nodes:
            # Look for benchmarks.json files
            benchmarks_files = [p for p in node.artifact_paths if p.name == "benchmarks.json"]

            if not benchmarks_files:
                # No benchmarks.json found, create empty record
                labels = _labels_from_node(node)
                records.append(
                    UnifiedResultRecord(
                        test_base_path=str(node.directory.relative_to(base_dir.resolve())),
                        distinguishing_labels=labels,
                        metrics={"no_benchmarks_found": True},
                        run_identity={"guidellm": True},
                        parse_notes=["No benchmarks.json file found"],
                    )
                )
                continue

            for benchmarks_file in benchmarks_files:
                benchmarks, config, file_warnings = self.parse_benchmarks_json(benchmarks_file)
                warnings.extend(file_warnings)

                # Create a unified record for each benchmark
                for benchmark in benchmarks:
                    labels = _labels_from_node(node)

                    # Convert benchmark to metrics dictionary
                    metrics = benchmark.to_dict()
                    if config:
                        metrics["configuration"] = config.to_dict()

                    records.append(
                        UnifiedResultRecord(
                            test_base_path=str(node.directory.relative_to(base_dir.resolve())),
                            distinguishing_labels=labels,
                            metrics=metrics,
                            run_identity={"guidellm": True},
                            parse_notes=[],
                        )
                    )

        logging.info(f"GuideLLM parser created {len(records)} unified result records")
        return ParseResult(records=records, warnings=warnings)
