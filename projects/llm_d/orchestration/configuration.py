from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from projects.core.library import config
from projects.llm_d.runtime import llmd_runtime


def load_runtime_configuration(
    *,
    cwd: Path | None = None,
    artifact_dir: Path | None = None,
    requested_preset: str | None = None,
    job_name: str | None = None,
):
    prepare_runtime_configuration(
        cwd=cwd,
        artifact_dir=artifact_dir,
        requested_preset=requested_preset,
        job_name=job_name,
    )
    return llmd_runtime.load_run_configuration()


def prepare_runtime_configuration(
    *,
    cwd: Path | None = None,
    artifact_dir: Path | None = None,
    requested_preset: str | None = None,
    job_name: str | None = None,
) -> None:
    cwd = cwd or Path.cwd()
    if artifact_dir is not None:
        os.environ["ARTIFACT_DIR"] = str(artifact_dir)

    llmd_runtime.init()
    config.reload(llmd_runtime.ORCHESTRATION_DIR)

    fournos_config = _load_fournos_config(cwd)
    resolved_preset = (
        requested_preset
        or fournos_config.get("preset")
        or config.project.get_config("runtime.default_preset")
    )
    if not resolved_preset:
        raise ValueError(
            "No llm_d preset was requested and no runtime.default_preset is configured"
        )
    if not config.project.get_preset(resolved_preset):
        raise ValueError(f"Unknown llm_d preset: {resolved_preset}")

    config.project.set_config("runtime.requested_preset", resolved_preset, print=False)
    config.project.apply_preset(resolved_preset)
    # CI/PR variable overrides must win over any values introduced by the preset.
    config.project.apply_config_overrides(log=False)

    selected_preset = config.project.get_config("runtime.selected_preset")
    resolved_job_name = job_name or fournos_config.get("job-name") or f"local-{selected_preset}"
    namespace_override = fournos_config.get("namespace")

    config.project.set_config("runtime.fournos_config", fournos_config, print=False)
    config.project.set_config("runtime.namespace_override", namespace_override, print=False)
    config.project.set_config("runtime.job_name", resolved_job_name, print=False)
    config.project.set_config("runtime.gpu_count", fournos_config.get("gpu-count"), print=False)


def _load_fournos_config(cwd: Path) -> dict[str, Any]:
    config_path = cwd / "fournos_config.yaml"
    if not config_path.exists():
        return {}

    data = llmd_runtime.load_yaml(config_path)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Unexpected FOURNOS config type in {config_path}: {type(data)}")
    return data
