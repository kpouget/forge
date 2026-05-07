"""Parse cache read/write with input fingerprint (FR-016)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

CACHE_SCHEMA_VERSION = "1"


def fingerprint_base_dir(base_dir: Path, plugin_module: str) -> str:
    """Stable hash over file paths + mtimes under base_dir."""
    base_dir = base_dir.resolve()
    entries: list[tuple[str, int, int]] = []
    for root, _dirs, files in os.walk(base_dir):
        for name in sorted(files):
            p = Path(root) / name
            try:
                st = p.stat()
            except OSError:
                continue
            rel = str(p.relative_to(base_dir))
            entries.append((rel, int(st.st_mtime_ns), st.st_size))
    entries.sort()
    payload = json.dumps(
        {"plugin_module": plugin_module, "files": entries},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def fingerprint_test_base(test_base_dir: Path, plugin_module: str) -> str:
    """Stable hash over file paths + mtimes under a single test base directory."""
    test_base_dir = test_base_dir.resolve()
    entries: list[tuple[str, int, int]] = []
    for root, _dirs, files in os.walk(test_base_dir):
        for name in sorted(files):
            p = Path(root) / name
            try:
                st = p.stat()
            except OSError:
                continue
            rel = str(p.relative_to(test_base_dir))
            entries.append((rel, int(st.st_mtime_ns), st.st_size))
    entries.sort()
    payload = json.dumps(
        {"plugin_module": plugin_module, "test_base": str(test_base_dir), "files": entries},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def default_cache_path(base_dir: Path, plugin_module: str) -> Path:
    safe = plugin_module.replace(".", "_")
    return base_dir.resolve() / ".caliper_cache" / f"{safe}_v{CACHE_SCHEMA_VERSION}.json"


def cache_path_for_test_base(test_base_dir: Path, plugin_module: str) -> Path:
    """Generate cache path for a specific test base directory."""
    safe = plugin_module.replace(".", "_")
    return test_base_dir.resolve() / ".caliper_cache" / f"{safe}_v{CACHE_SCHEMA_VERSION}.json"


def write_test_base_cache(
    test_base_dir: Path,
    *,
    plugin_module: str,
    test_base_records: list,
    fingerprint: str,
) -> Path:
    """Write cache for a single test base."""

    def rec_to_dict(r: Any) -> dict[str, Any]:
        """Convert UnifiedResultRecord to dict."""
        return {
            "test_base_path": r.test_base_path,
            "distinguishing_labels": r.distinguishing_labels,
            "metrics": r.metrics,
            "run_identity": r.run_identity,
            "parse_notes": r.parse_notes,
        }

    cache_path = cache_path_for_test_base(test_base_dir, plugin_module)
    doc = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "plugin_module": plugin_module,
        "test_base_dir": str(test_base_dir),
        "input_fingerprint": fingerprint,
        "records": [rec_to_dict(r) for r in test_base_records],
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return cache_path


def read_test_base_cache(test_base_dir: Path, plugin_module: str) -> dict[str, Any] | None:
    """Read cache for a single test base and convert records back to objects."""
    from projects.caliper.engine.model import UnifiedResultRecord  # noqa: PLC0415

    def dict_to_rec(d: dict[str, Any]) -> UnifiedResultRecord:
        """Convert dict back to UnifiedResultRecord."""
        return UnifiedResultRecord(
            test_base_path=d["test_base_path"],
            distinguishing_labels=d["distinguishing_labels"],
            metrics=d["metrics"],
            run_identity=d["run_identity"],
            parse_notes=d["parse_notes"],
        )

    cache_path = cache_path_for_test_base(test_base_dir, plugin_module)
    if not cache_path.is_file():
        return None

    raw_data = json.loads(cache_path.read_text(encoding="utf-8"))

    # Convert record dicts back to UnifiedResultRecord objects
    if "records" in raw_data:
        raw_data["records"] = [dict_to_rec(r) for r in raw_data["records"]]

    return raw_data


def test_base_cache_is_valid(
    cached: dict[str, Any],
    *,
    expected_fingerprint: str,
    plugin_module: str,
    test_base_dir: Path,
) -> bool:
    """Check if test base cache is valid."""
    if cached.get("schema_version") != CACHE_SCHEMA_VERSION:
        return False
    if cached.get("plugin_module") != plugin_module:
        return False
    if cached.get("test_base_dir") != str(test_base_dir):
        return False
    if cached.get("input_fingerprint") != expected_fingerprint:
        return False
    return True


def write_cache(
    path: Path,
    *,
    unified_model_dict: dict[str, Any],
    fingerprint: str,
    plugin_module: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "plugin_module": plugin_module,
        "input_fingerprint": fingerprint,
        "unified_model": unified_model_dict,
    }
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def read_cache(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def cache_is_valid(
    cached: dict[str, Any],
    *,
    expected_fingerprint: str,
    plugin_module: str,
) -> bool:
    if cached.get("schema_version") != CACHE_SCHEMA_VERSION:
        return False
    if cached.get("plugin_module") != plugin_module:
        return False
    if cached.get("input_fingerprint") != expected_fingerprint:
        return False
    return True
