from __future__ import annotations

import pytest

from projects.llm_d.orchestration import pr_args


def test_parse_project_directives_extracts_positional_preset() -> None:
    overrides, directives = pr_args.parse_project_directives("/test fournos llm_d smoke")

    assert overrides == {
        "runtime.default_preset": "smoke",
        "ci_job.args": [],
    }
    assert directives == ["/test fournos llm_d smoke"]


def test_parse_project_directives_ignores_test_without_preset() -> None:
    overrides, directives = pr_args.parse_project_directives("/test fournos llm_d")

    assert overrides == {}
    assert directives == []


def test_parse_project_directives_ignores_other_projects() -> None:
    overrides, directives = pr_args.parse_project_directives("/test fournos skeleton smoke")

    assert overrides == {}
    assert directives == []


def test_parse_project_directives_rejects_multiple_presets() -> None:
    with pytest.raises(ValueError, match="at most one preset"):
        pr_args.parse_project_directives("/test fournos llm_d smoke benchmark-short")
