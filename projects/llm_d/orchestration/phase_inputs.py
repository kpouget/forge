from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from projects.llm_d.orchestration.runtime_config import ResolvedConfig, load_yaml, write_yaml


@dataclass(frozen=True)
class CleanupInputs:
    artifact_dir: Path
    namespace: str
    platform: dict[str, Any]
    benchmark: dict[str, Any] | None


@dataclass(frozen=True)
class PrepareModelCacheInputs:
    artifact_dir: Path
    preset_name: str
    namespace: str
    namespace_is_managed: bool
    model_key: str
    model: dict[str, Any]
    model_cache: dict[str, Any]


@dataclass(frozen=True)
class PrepareInputs:
    artifact_dir: Path
    config_dir: Path
    preset_name: str
    namespace: str
    namespace_is_managed: bool
    platform: dict[str, Any]
    model_key: str
    model: dict[str, Any]
    model_cache: dict[str, Any]
    benchmark: dict[str, Any] | None


@dataclass(frozen=True)
class TestInputs:
    artifact_dir: Path
    config_dir: Path
    preset_name: str
    namespace: str
    platform: dict[str, Any]
    model_key: str
    model: dict[str, Any]
    scheduler_profile_key: str
    scheduler_profile: dict[str, Any] | None
    model_cache: dict[str, Any]
    smoke_request: dict[str, Any]
    benchmark: dict[str, Any] | None


def write_cleanup_inputs(config: ResolvedConfig) -> Path:
    path = config.artifact_dir / "_meta" / "cleanup.inputs.yaml"
    write_yaml(
        path,
        {
            "artifact_dir": str(config.artifact_dir),
            "namespace": config.namespace,
            "platform": config.platform,
            "benchmark": config.benchmark,
        },
    )
    return path


def write_prepare_model_cache_inputs(config: ResolvedConfig) -> Path:
    path = config.artifact_dir / "_meta" / "prepare_model_cache.inputs.yaml"
    write_yaml(
        path,
        {
            "artifact_dir": str(config.artifact_dir),
            "preset_name": config.preset_name,
            "namespace": config.namespace,
            "namespace_is_managed": config.namespace_is_managed,
            "model_key": config.model_key,
            "model": config.model,
            "model_cache": config.model_cache,
        },
    )
    return path


def write_prepare_inputs(config: ResolvedConfig) -> Path:
    path = config.artifact_dir / "_meta" / "prepare.inputs.yaml"
    write_yaml(
        path,
        {
            "artifact_dir": str(config.artifact_dir),
            "config_dir": str(config.config_dir),
            "preset_name": config.preset_name,
            "namespace": config.namespace,
            "namespace_is_managed": config.namespace_is_managed,
            "platform": config.platform,
            "model_key": config.model_key,
            "model": config.model,
            "model_cache": config.model_cache,
            "benchmark": config.benchmark,
        },
    )
    return path


def write_test_inputs(config: ResolvedConfig) -> Path:
    path = config.artifact_dir / "_meta" / "test.inputs.yaml"
    write_yaml(
        path,
        {
            "artifact_dir": str(config.artifact_dir),
            "config_dir": str(config.config_dir),
            "preset_name": config.preset_name,
            "namespace": config.namespace,
            "platform": config.platform,
            "model_key": config.model_key,
            "model": config.model,
            "scheduler_profile_key": config.scheduler_profile_key,
            "scheduler_profile": config.scheduler_profile,
            "model_cache": config.model_cache,
            "smoke_request": config.smoke_request,
            "benchmark": config.benchmark,
        },
    )
    return path


def load_cleanup_inputs(path: str | Path) -> CleanupInputs:
    payload = load_yaml(Path(path))
    return CleanupInputs(
        artifact_dir=Path(payload["artifact_dir"]),
        namespace=payload["namespace"],
        platform=payload["platform"],
        benchmark=payload["benchmark"],
    )


def load_prepare_model_cache_inputs(path: str | Path) -> PrepareModelCacheInputs:
    payload = load_yaml(Path(path))
    return PrepareModelCacheInputs(
        artifact_dir=Path(payload["artifact_dir"]),
        preset_name=payload["preset_name"],
        namespace=payload["namespace"],
        namespace_is_managed=payload["namespace_is_managed"],
        model_key=payload["model_key"],
        model=payload["model"],
        model_cache=payload["model_cache"],
    )


def load_prepare_inputs(path: str | Path) -> PrepareInputs:
    payload = load_yaml(Path(path))
    return PrepareInputs(
        artifact_dir=Path(payload["artifact_dir"]),
        config_dir=Path(payload["config_dir"]),
        preset_name=payload["preset_name"],
        namespace=payload["namespace"],
        namespace_is_managed=payload["namespace_is_managed"],
        platform=payload["platform"],
        model_key=payload["model_key"],
        model=payload["model"],
        model_cache=payload["model_cache"],
        benchmark=payload["benchmark"],
    )


def load_test_inputs(path: str | Path) -> TestInputs:
    payload = load_yaml(Path(path))
    return TestInputs(
        artifact_dir=Path(payload["artifact_dir"]),
        config_dir=Path(payload["config_dir"]),
        preset_name=payload["preset_name"],
        namespace=payload["namespace"],
        platform=payload["platform"],
        model_key=payload["model_key"],
        model=payload["model"],
        scheduler_profile_key=payload["scheduler_profile_key"],
        scheduler_profile=payload["scheduler_profile"],
        model_cache=payload["model_cache"],
        smoke_request=payload["smoke_request"],
        benchmark=payload["benchmark"],
    )


def cleanup_inputs_from_prepare(inputs: PrepareInputs) -> CleanupInputs:
    return CleanupInputs(
        artifact_dir=inputs.artifact_dir,
        namespace=inputs.namespace,
        platform=inputs.platform,
        benchmark=inputs.benchmark,
    )


def prepare_model_cache_inputs_from_prepare(inputs: PrepareInputs) -> PrepareModelCacheInputs:
    return PrepareModelCacheInputs(
        artifact_dir=inputs.artifact_dir,
        preset_name=inputs.preset_name,
        namespace=inputs.namespace,
        namespace_is_managed=inputs.namespace_is_managed,
        model_key=inputs.model_key,
        model=inputs.model,
        model_cache=inputs.model_cache,
    )
