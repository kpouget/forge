#!/usr/bin/env python3
"""
MCP Gateway Project PR Arguments Parser

Parses mcp_gateway-specific directives from PR trigger comments.

Supported syntax::

    /test fournos mcp_gateway smoke
    /cluster agentic-cpt-8xa100
    /pipeline forge-full
    /version 0.7.0
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_supported_mcp_gateway_directives() -> dict[str, str]:
    """Supported mcp_gateway-specific PR trigger directives."""
    return {
        "/test fournos mcp_gateway PRESET": (
            "Select a preset from the /test line.\n"
            "Format: /test fournos mcp_gateway PRESET\n"
            "Example: /test fournos mcp_gateway smoke\n"
            "Available presets: smoke, baseline, scale-out, demo\n"
            "Effect: Sets runtime.default_preset and clears ci_job.args."
        ),
        "/version": (
            "Set the MCP Gateway version to install.\n"
            "Format: /version VERSION\n"
            "Example: /version 0.7.0\n"
            "Effect: Sets infrastructure.mcp_gateway_version in configuration."
        ),
    }


def _parse_test_line(line: str) -> list[str] | None:
    """Return trailing mcp_gateway test args for a matching /test fournos line."""
    if not line.startswith("/test "):
        return None

    parts = line[6:].strip().split()
    if len(parts) < 2:
        return None

    test_name, project_name, *args = parts
    if test_name != "fournos" or project_name != "mcp_gateway":
        return None

    return args


def parse_project_directives(comment_text: str) -> tuple[dict[str, Any], list[str]]:
    """
    Parse mcp_gateway-specific directives from PR trigger comments.

    Args:
        comment_text: Text from PR trigger comment

    Returns:
        Tuple of (configuration overrides dict, list of parsed directive lines)
    """
    config_overrides: dict[str, Any] = {}
    parsed_directives: list[str] = []

    for raw_line in comment_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # Parse preset from /test line
        args = _parse_test_line(line)
        if args is not None:
            if args:
                if len(args) > 1:
                    raise ValueError(
                        "mcp_gateway accepts at most one preset in "
                        "'/test fournos mcp_gateway PRESET'"
                    )
                preset = args[0]
                config_overrides.update(
                    {
                        "runtime.default_preset": preset,
                        "ci_job.args": [],
                    }
                )
                parsed_directives.append(line)
                logger.info("Parsed mcp_gateway preset: %s", preset)
            continue

        # Parse /version directive
        if line.startswith("/version"):
            version = line.removeprefix("/version").strip()
            if not version:
                raise ValueError(
                    "Invalid /version directive: version cannot be empty. Example: /version 0.7.0"
                )
            config_overrides["infrastructure.mcp_gateway_version"] = version
            parsed_directives.append(line)
            logger.info("Parsed mcp_gateway version: %s", version)

    return config_overrides, parsed_directives
