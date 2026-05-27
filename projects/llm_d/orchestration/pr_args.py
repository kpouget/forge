#!/usr/bin/env python3
"""
llm_d Project PR Arguments Parser

Parses llm_d-specific behavior from PR trigger comments.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_supported_llm_d_directives() -> dict[str, str]:
    """
    Get a dictionary of supported llm_d-specific PR trigger forms.

    Returns:
        Dictionary mapping trigger forms to detailed descriptions
    """
    return {
        "/test fournos llm_d PRESET": """Select an llm_d preset from the /test line.
                                       Format: /test fournos llm_d PRESET
                                       Example: /test fournos llm_d smoke
                                       Effect: Sets runtime.default_preset to PRESET and clears
                                               ci_job.args so the preset is selected only through
                                               llm_d runtime configuration.""",
    }


def _parse_test_line(line: str) -> list[str] | None:
    """Return trailing llm_d test args for a matching /test fournos line."""
    if not line.startswith("/test "):
        return None

    parts = line[6:].strip().split()
    if len(parts) < 2:
        return None

    test_name, project_name, *args = parts
    if test_name != "fournos" or project_name != "llm_d":
        return None

    return args


def parse_project_directives(comment_text: str) -> tuple[dict[str, Any], list[str]]:
    """
    Parse llm_d-specific behavior from PR trigger comments.

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

        args = _parse_test_line(line)
        if args is None:
            continue

        if not args:
            continue

        if len(args) > 1:
            raise ValueError("llm_d accepts at most one preset in '/test fournos llm_d PRESET'")

        preset = args[0]
        config_overrides.update(
            {
                "runtime.default_preset": preset,
                "ci_job.args": [],
            }
        )
        parsed_directives.append(line)
        logger.info(f"Parsed llm_d preset from test directive: {line}")

    return config_overrides, parsed_directives
