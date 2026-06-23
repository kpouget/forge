from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_supported_rhaiis_directives() -> dict[str, str]:
    return {
        "/test fournos rhaiis PRESET [PRESET...]": """Select rhaiis presets from the /test line.
            Format: /test fournos rhaiis PRESET [PRESET...]
            Example: /test fournos rhaiis ci-quick
                     /test fournos rhaiis llama-8b profile1
            Available presets: ci-quick, llama-8b, granite-8b,
              profile1-4, nvidia, amd (see presets.d/presets.yaml).""",
    }


def _parse_test_line(line: str) -> list[str] | None:
    if not line.startswith("/test "):
        return None

    parts = line[6:].strip().split()
    if len(parts) < 2:
        return None

    test_name, project_name, *args = parts
    if test_name != "fournos" or project_name != "rhaiis":
        return None

    return args


def parse_project_directives(comment_text: str) -> tuple[dict[str, Any], list[str]]:
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

        config_overrides["ci_job.args"] = args
        parsed_directives.append(line)
        logger.info("Parsed rhaiis presets from test directive: %s", line)

    return config_overrides, parsed_directives
