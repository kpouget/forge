"""KPI output format transformations and utilities."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

from projects.caliper.engine.kpi.dataclasses import (
    HierarchicalKpi,
    HierarchicalKpiFormat,
    HierarchicalTestEntry,
    TestMetadata,
)

logger = logging.getLogger(__name__)


def transform_kpis_to_hierarchical_format(kpis: list[dict], model) -> HierarchicalKpiFormat:
    """
    Transform flat KPI list into hierarchical JSON structure using dataclasses.

    Groups KPIs by test (run_id) using HierarchicalTestEntry dataclasses,
    with TestMetadata for test metadata and direct field access from
    KpiCatalogEntry dataclasses for improved type safety.

    Args:
        kpis: List of flat KPI records from compute_kpis
        model: Unified model for accessing plugin metadata

    Returns:
        HierarchicalKpiFormat dataclass containing structured test entries
        with TestMetadata and KPI data
    """

    if not kpis:
        return HierarchicalKpiFormat()

    # Group KPIs by test (run_id) using dataclasses
    tests_data: dict[str, HierarchicalTestEntry] = {}

    # Get KPI function metadata from the plugin module

    plugin_module_obj = __import__(model.plugin_module, fromlist=[""])
    kpi_catalog = plugin_module_obj.get_plugin().kpi_catalog()

    # Build KPI models index from KpiCatalogEntry dataclasses
    kpi_models = {entry.kpi_id: entry for entry in kpi_catalog}

    # First pass: determine which labels vary across KPIs in the same run
    run_label_values: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for kpi in kpis:
        run_id = kpi.get("run_id", "unknown")
        for k, v in kpi.get("labels", {}).items():
            run_label_values[run_id][k].add(str(v))

    for kpi in kpis:
        kpi_id = kpi.get("kpi_id")
        if kpi_id not in kpi_models:
            logger.warning(f"{kpi_id} not found in the KPI metadata, ignoring.")
            continue
        kpi_model = kpi_models[kpi_id]

        run_id = kpi.get("run_id", "unknown")

        # Get or create test entry using dataclass
        if run_id not in tests_data:
            tests_data[run_id] = HierarchicalTestEntry(run_id=run_id)

        test_data = tests_data[run_id]

        # Update labels
        test_data.labels.update(kpi["labels"])

        # Store test metadata from first KPI
        if not test_data.metadata.timestamp:
            test_data.metadata = TestMetadata(
                timestamp=kpi.get("timestamp", ""),
                run_id=run_id,
            )

        raw_value = kpi.get("value")

        # Apply tuple-pair structural transform only for confirmed curve KPIs
        if kpi_model.is_curve:
            final_value = {
                "data_points": [{"x": float(x), "y": float(y)} for x, y in raw_value],
                "count": len(raw_value),
            }
        else:
            final_value = raw_value

        # Build output record using HierarchicalKpi dataclass
        kpi_output = HierarchicalKpi(
            id=kpi_model.kpi_id,  # Use 'id' field for schema-v2 output
            name=kpi_model.name,
            value=final_value,
            unit=kpi_model.unit,
            higher_is_better=kpi_model.higher_is_better,
            is_curve=kpi_model.is_curve,
            help=kpi_model.help,
        )

        # Add curve-specific fields only if it's a curve KPI
        if kpi_model.is_curve:
            kpi_output.x_unit = kpi_model.x_unit
            kpi_output.x_help = kpi_model.x_help
            kpi_output.y_unit = kpi_model.y_unit
            kpi_output.y_help = kpi_model.y_help

        test_data.kpis.append(kpi_output)

    # Convert to hierarchical format using dataclass
    hierarchical_format = HierarchicalKpiFormat(
        schema_version="2",
        tests=list(tests_data.values()),
    )

    return hierarchical_format


def write_kpis_in_format(
    kpis: list[dict], output_file: Path, format_type: str = "hierarchical", model: Any = None
) -> None:
    """
    Write KPIs to file in the specified format.

    Args:
        kpis: List of KPI records
        output_file: Path to output file
        format_type: Format type - "hierarchical" (default) or "jsonl"
        model: Unified model (required for hierarchical format)
    """
    output_file.parent.mkdir(parents=True, exist_ok=True)

    if format_type == "hierarchical":
        if model is None:
            raise ValueError("Model is required for hierarchical format")

        # Transform to hierarchical format (schema v2)
        hierarchical_format = transform_kpis_to_hierarchical_format(kpis, model)

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(hierarchical_format.to_dict(), f, indent=2, ensure_ascii=False)
            # Add EOL at EOF if we have data
            if kpis:
                f.write("\n")

    elif format_type == "jsonl":
        # Write as JSONL (schema v1) - matches original behavior
        text = "\n".join(json.dumps(kpi, ensure_ascii=False) for kpi in kpis) + (
            "\n" if kpis else ""
        )
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(text)

    else:
        raise ValueError(f"Unknown format type: {format_type}. Use 'hierarchical' or 'jsonl'")


def flatten_hierarchical_kpis(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Lossless conversion from schema_version=2 hierarchical KPI doc to flat records.

    Each output record contains:
    - All test-level fields: run_id, labels, metadata
    - All kpi-level fields verbatim, with 'id' renamed to 'kpi_id'

    The value is kept as-is (no conversion). Callers that need schema-v1 value
    representation should apply _convert_to_schema_v1_value() separately.
    """
    records: list[dict[str, Any]] = []
    for test in data.get("tests", []):
        test_base = {
            "run_id": test.get("run_id"),
            "labels": test.get("labels", {}),
            "metadata": test.get("metadata", {}),
        }
        for kpi in test.get("kpis", []):
            record = dict(test_base)
            record["kpi_id"] = kpi.get("id")
            for k, v in kpi.items():
                if k == "id":
                    pass
                elif k == "labels":
                    record[k] = {} | test_base["labels"] | v
                else:
                    record[k] = v

            records.append(record)
    return records


def _convert_to_schema_v1_value(raw_value: Any) -> Any:
    """
    Convert structured KPI value back to schema-v1 list-of-pairs representation.

    Args:
        raw_value: The KPI value, either scalar or structured with data_points/count

    Returns:
        Converted value - list of pairs for 2D data, scalar values unchanged
    """
    if isinstance(raw_value, dict):
        # Check if this is a structured value with data_points
        if "data_points" in raw_value and isinstance(raw_value["data_points"], list):
            # Convert data_points list back to list-of-pairs format
            data_points = raw_value["data_points"]
            return [
                [point.get("x"), point.get("y")]
                for point in data_points
                if isinstance(point, dict) and "x" in point and "y" in point
            ]
        # For other dict structures, return as-is (preserve existing format)
        return raw_value
    else:
        # Preserve scalar values unchanged
        return raw_value


def read_kpis_from_file(file_path: Path) -> list[dict]:
    """
    Read KPIs from a file, handling both JSONL and hierarchical JSON formats.

    Args:
        file_path: Path to the KPI file

    Returns:
        List of KPI records in flat format
    """
    kpis = []

    with open(file_path, encoding="utf-8") as f:
        content = f.read().strip()

    if not content:
        return kpis

    try:
        # Try to parse as JSON (hierarchical format)
        data = json.loads(content)

        if isinstance(data, dict) and data.get("schema_version") == "2":
            for rec in flatten_hierarchical_kpis(data):
                rec["value"] = _convert_to_schema_v1_value(rec.get("value"))
                kpis.append(rec)
        else:
            # Unknown JSON format
            raise ValueError("Unknown JSON format")

    except json.JSONDecodeError:
        # Try to parse as JSONL
        for line in content.splitlines():
            line = line.strip()
            if line:
                try:
                    kpi = json.loads(line)
                    kpis.append(kpi)
                except json.JSONDecodeError:
                    continue  # Skip invalid lines

    return kpis
