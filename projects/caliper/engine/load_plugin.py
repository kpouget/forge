"""Load plugin module and validate required callables."""

from __future__ import annotations

import importlib
from typing import Any, cast

from projects.caliper.engine.model import PostProcessingPlugin


def load_plugin(module_path: str) -> PostProcessingPlugin:
    """
    FR-014: import plugin module; fail with actionable error.

    Convention: module exposes ``get_plugin() -> PostProcessingPlugin`` or
    attribute ``plugin`` implementing the protocol.
    """
    try:
        mod = importlib.import_module(module_path)
    except ModuleNotFoundError as e:
        raise RuntimeError(
            f"Cannot import plugin module {module_path!r}: {e}. "
            "Check PYTHONPATH and package installation."
        ) from e
    except Exception as e:  # noqa: BLE001 — surface import-time errors
        raise RuntimeError(f"Failed loading plugin module {module_path!r}: {e}") from e

    plugin: Any = None
    if hasattr(mod, "get_plugin"):
        plugin = mod.get_plugin()
    elif hasattr(mod, "plugin"):
        plugin = mod.plugin
    else:
        raise RuntimeError(
            f"Plugin module {module_path!r} must define get_plugin() or 'plugin' "
            "returning a PostProcessingPlugin implementation."
        )
    if plugin is None:
        raise RuntimeError(f"Plugin module {module_path!r} returned no plugin.")
    return cast(PostProcessingPlugin, plugin)
