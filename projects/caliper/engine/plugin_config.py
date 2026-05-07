"""Locate post-processing manifest and resolve plugin module."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

MANIFEST_FILENAMES = ("caliper.yaml", "forge-postprocess.yaml", "postprocess.yaml")

# Keep in sync with CLI flags in projects/caliper/cli/main.py
_CLI_ARTIFACT_TREE = "`--artifacts-dir` / `--base-dir`"
_CLI_PLUGIN_MODULE = "`--plugin-module` / `--plugin`"


def load_manifest_file(path: Path) -> dict[str, Any]:
    """Load YAML manifest; raise ValueError on invalid content."""
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError(f"Manifest must be a mapping at top level: {path}")
    return data


def _find_manifest_under_base(base_dir: Path) -> Path | None:
    base_dir = base_dir.resolve()
    for name in MANIFEST_FILENAMES:
        candidate = base_dir / name
        if candidate.is_file():
            return candidate
    return None


def resolve_manifest_path(
    base_dir: Path,
    postprocess_config: Path | None,
) -> tuple[Path | None, dict[str, Any] | None]:
    """Return (path, data) if a manifest was found/loaded."""
    if postprocess_config is not None:
        p = postprocess_config.resolve()
        if not p.is_file():
            raise FileNotFoundError(f"Post-processing manifest not found: {p}")
        return p, load_manifest_file(p)
    found = _find_manifest_under_base(base_dir)
    if found is None:
        return None, None
    return found, load_manifest_file(found)


def resolve_plugin_module_string(
    *,
    base_dir: Path,
    postprocess_config: Path | None,
    cli_plugin: str | None,
) -> tuple[str, Path | None]:
    """
    FR-002 resolution: CLI plugin module flag overrides manifest ``plugin_module``.
    Returns (module_string, manifest_path_or_none).
    """
    manifest_path, data = resolve_manifest_path(base_dir, postprocess_config)
    if cli_plugin:
        return cli_plugin.strip(), manifest_path
    if data is None:
        raise ValueError(
            "No plugin module: set plugin_module in "
            f"{'/'.join(MANIFEST_FILENAMES)} under {_CLI_ARTIFACT_TREE}, "
            f"or pass {_CLI_PLUGIN_MODULE}, "
            "or use --postprocess-config PATH with a manifest that declares plugin_module."
        )
    mod = data.get("plugin_module")
    if not mod or not isinstance(mod, str):
        raise ValueError(
            f"Manifest {manifest_path} must declare a non-empty string 'plugin_module', "
            f"or pass {_CLI_PLUGIN_MODULE}."
        )
    return mod.strip(), manifest_path
