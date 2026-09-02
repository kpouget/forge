"""KPI definitions and computation for Skeleton Caliper plugin."""

from __future__ import annotations

import inspect
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from projects.caliper.engine.kpi import (
    # KPI function decorators and utilities
    Format,
    HigherBetter,
    KpiCatalogEntry,
    KPIMetadata,
    KpiRecord,
    LowerBetter,
    build_catalog_from_functions,
    create_label_extractor,
    get_kpi_functions,
    is_curve_kpi,
)
from projects.caliper.engine.model import UnifiedRunModel


# KPI function definitions with annotations
@HigherBetter()
@Format("{:.2f}")
@KPIMetadata(help="Number of requests processed per second", unit="req/s")
def kpi_skeleton_throughput_rps(unified_record) -> float:
    """Throughput KPI."""
    value = unified_record.metrics.get("throughput")
    if value is None:
        raise ValueError("throughput metric not found")
    return float(value)


@LowerBetter()
@Format("{:.2f}")
@KPIMetadata(help="Average response latency in milliseconds", unit="ms")
def kpi_skeleton_latency_ms(unified_record) -> float:
    """Latency KPI."""
    value = unified_record.metrics.get("latency_ms")
    if value is None:
        raise ValueError("latency_ms metric not found")
    return float(value)


# Label extractor for extracting version/date from test records
LABEL_EXTRACTOR = create_label_extractor(
    {
        "scenario": "distinguishing_labels.scenario",
        "workload": "distinguishing_labels.workload",
    }
)


def extract_version(record) -> str:
    """Extract version (date) from test record for analysis."""
    # Check if version/date is explicitly in labels
    labels = record.distinguishing_labels
    if "version" in labels:
        return str(labels["version"])
    if "date" in labels:
        return str(labels["date"])

    # Try to extract date from test path
    test_path = record.test_base_path
    date_pattern = r"\b(\d{4}-\d{2}-\d{2})\b"
    match = re.search(date_pattern, test_path)
    if match:
        return match.group(1)

    # Try to extract date from directory structure
    path_parts = Path(test_path).parts
    for part in path_parts:
        match = re.search(date_pattern, part)
        if match:
            return match.group(1)

    # Fallback: use current date
    return datetime.now(UTC).strftime("%Y-%m-%d")


class SkeletonKpiHandler:
    """Simple KPI handler for skeleton project."""

    @staticmethod
    def get_catalog() -> list[KpiCatalogEntry]:
        """Return KPI catalog entries from annotated functions."""
        current_module = inspect.getmodule(SkeletonKpiHandler)
        return build_catalog_from_functions(current_module)

    @staticmethod
    def compute_kpis(model: UnifiedRunModel) -> list[dict[str, Any]]:
        """Compute KPI values using annotated functions."""
        current_module = inspect.getmodule(SkeletonKpiHandler)
        kpi_functions = get_kpi_functions(current_module)
        kpis = []
        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        for record in model.unified_result_records:
            # Extract labels using the label extractor
            labels = LABEL_EXTRACTOR.extract(record)
            labels.update(record.distinguishing_labels)
            labels["version"] = extract_version(record)

            # Compute each KPI
            for kpi_id, kpi_func in kpi_functions.items():
                try:
                    value = kpi_func(record)
                except (TypeError, ValueError, KeyError):
                    continue  # Skip failed KPIs

                # Skip null/empty values
                if value is None or (isinstance(value, list) and not value):
                    continue

                # Create KPI record using annotations
                kpi_record = KpiRecord(
                    kpi_id=kpi_id,
                    value=value,
                    unit=kpi_func._kpi_unit,
                    run_id=record.test_base_path,
                    timestamp=timestamp,
                    labels=labels,
                    higher_is_better=kpi_func._kpi_higher_is_better,
                    is_curve=is_curve_kpi(kpi_func),
                    y_unit=kpi_func._kpi_y_unit,
                    y_help=kpi_func._kpi_y_help,
                )

                # Add x-axis metadata for curve KPIs
                if is_curve_kpi(kpi_func):
                    kpi_record.x_unit = kpi_func._kpi_x_unit
                    kpi_record.x_help = kpi_func._kpi_x_help

                kpis.append(kpi_record.to_dict())

        return kpis
