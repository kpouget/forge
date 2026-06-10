from __future__ import annotations

from pathlib import Path

import pytest

from projects.core.library import config as core_config
from projects.core.library import env
from projects.llm_d.orchestration import runtime_config

PROJECT_ORCHESTRATION_DIR = Path(__file__).resolve().parents[1] / "orchestration"


@pytest.fixture(autouse=True)
def _reset_project_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path / "artifacts"))
    env.init()
    core_config.project = None
    yield
    core_config.project = None


def _init_project_config() -> None:
    core_config.init(PROJECT_ORCHESTRATION_DIR)


def test_deployment_presets_resolve_scheduler_profiles() -> None:
    _init_project_config()

    core_config.project.apply_preset("deployment-approximate-prefix-cache")
    assert runtime_config.get_scheduler_profile_key() == "approximate"

    core_config.project.apply_preset("deployment-precise-prefix-cache")
    assert runtime_config.get_scheduler_profile_key() == "precise"

    core_config.project.apply_preset("deployment-distributed-default")
    assert runtime_config.get_scheduler_profile_key() == "default"


def test_smoke_presets_inherit_deployment_scheduler_modes() -> None:
    _init_project_config()

    core_config.project.apply_preset("smoke")
    assert runtime_config.get_scheduler_profile_key() == "approximate"
    assert runtime_config.get_model_key() == "qwen3-0-6b"
    assert runtime_config.get_benchmark_config() is None

    core_config.project.apply_preset("smoke-precise")
    assert runtime_config.get_scheduler_profile_key() == "precise"
    assert runtime_config.get_model_key() == "qwen3-0-6b"

    core_config.project.apply_preset("smoke-default-scheduler")
    assert runtime_config.get_scheduler_profile_key() == "default"
    assert runtime_config.get_model_key() == "qwen3-0-6b"


def test_benchmark_workloads_are_available() -> None:
    _init_project_config()

    concurrent = core_config.project.get_config(
        "workloads.benchmarks.concurrent-1k-1k", print=False
    )
    heavy = core_config.project.get_config("workloads.benchmarks.heavy-heterogeneous", print=False)
    multi_turn = core_config.project.get_config("workloads.benchmarks.multi-turn", print=False)

    assert concurrent["args"]["rates"] == [300, 200, 100, 50, 1]
    assert concurrent["timeout_seconds"] == 3600
    assert heavy["args"]["max_seconds"] == 600
    assert "prompt_tokens_stdev=8500" in heavy["args"]["data"]
    assert "output_tokens_max=8000" in heavy["args"]["data"]
    assert multi_turn["args"]["rates"] == [32, 64, 128, 256, 512]
    assert "turns=5" in multi_turn["args"]["data"]
    assert "prefix_count={2*rate}" in multi_turn["args"]["data"]
    assert multi_turn["args"]["max_requests"] == "{10*rate}"
