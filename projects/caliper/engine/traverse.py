"""Discover test base directories via __test_labels__.yaml."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from projects.caliper.engine.model import TestBaseNode

MARKER = "__test_labels__.yaml"


def discover_test_bases(base_dir: Path) -> list[TestBaseNode]:
    """Walk base_dir; each directory containing MARKER becomes a TestBaseNode."""
    base_dir = base_dir.resolve()
    if not base_dir.is_dir():
        raise FileNotFoundError(f"Base directory does not exist: {base_dir}")

    nodes: list[TestBaseNode] = []
    for dirpath, _dirnames, filenames in os.walk(base_dir, topdown=True):
        if MARKER not in filenames:
            continue
        path = Path(dirpath)
        marker_path = path / MARKER
        labels = _load_labels(marker_path)
        nodes.append(
            TestBaseNode(
                directory=path,
                labels=labels,
                artifact_paths=_list_files_under(path, exclude_marker=True),
            )
        )
    return sorted(nodes, key=lambda n: str(n.directory))


def _load_labels(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Invalid {MARKER}: top level must be a mapping: {path}")
    return data


def _list_files_under(dirpath: Path, *, exclude_marker: bool) -> list[Path]:
    out: list[Path] = []
    for p in sorted(dirpath.rglob("*")):
        if p.is_file() and (not exclude_marker or p.name != MARKER):
            out.append(p)
    return out
