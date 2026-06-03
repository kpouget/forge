#!/usr/bin/env python3
"""
LLM-D Project CI Operations

"""

import logging
import os
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import click
import yaml

from projects.core.ci_entrypoint.fournos_resolve import create_fournos_resolve_command

# Use core K8s utilities instead of llmd_runtime
from projects.core.library import ci as ci_lib
from projects.core.library import config, env, run, vault
from projects.core.library.export import caliper_export_command
from projects.llm_d.orchestration import runtime_config
from projects.llm_d.orchestration.cleanup_phase import run as cleanup_toolbox_run
from projects.llm_d.orchestration.prepare_sequence import run_prepare_sequence
from projects.llm_d.orchestration.runtime_config import init as runtime_init
from projects.llm_d.orchestration.test_phase import run as test_toolbox_run

logger = logging.getLogger(__name__)


def init():
    """Initialize LLM-D orchestration environment"""
    env.init()
    run.init()
    runtime_init()
    config.init(Path(__file__).parent)


def _load_fournos_config(cwd: Path) -> dict[str, Any]:
    """Load fournos_config.yaml if it exists"""
    config_path = cwd / "fournos_config.yaml"
    if not config_path.exists():
        return {}

    with open(config_path) as f:
        data = yaml.safe_load(f)

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Unexpected FOURNOS config type in {config_path}: {type(data)}")
    return data


def load_runtime_configuration(*, cwd=None, artifact_dir=None):
    """Load LLM-D runtime configuration using core config + LLM-D specific settings"""
    cwd = Path(cwd) if cwd else Path.cwd()

    # Initialize the environment using core pattern
    init()

    # Load LLM-D specific fournos config
    fournos_config = _load_fournos_config(cwd)

    # Resolve preset with LLM-D specific logic
    requested_preset = (
        os.environ.get("FORGE_PRESET")
        or fournos_config.get("preset")
        or config.project.get_config("runtime.default_preset")
    )

    if not requested_preset:
        raise ValueError(
            "No llm_d preset was requested and no runtime.default_preset is configured"
        )

    if not config.project.get_preset(requested_preset):
        raise ValueError(f"Unknown llm_d preset: {requested_preset}")

    config.project.set_config("runtime.requested_preset", requested_preset, print=False)
    config.project.apply_preset(requested_preset)
    config.project.apply_config_overrides(log=False)

    # Set LLM-D specific runtime config
    selected_preset = config.project.get_config("runtime.selected_preset")
    job_name = (
        os.environ.get("FORGE_JOB_NAME")
        or fournos_config.get("job-name")
        or f"local-{selected_preset}"
    )
    namespace_override = fournos_config.get("namespace")

    config.project.set_config("runtime.fournos_config", fournos_config, print=False)
    config.project.set_config("runtime.namespace_override", namespace_override, print=False)
    config.project.set_config("runtime.job_name", job_name, print=False)
    config.project.set_config("runtime.gpu_count", fournos_config.get("gpu-count"), print=False)

    # Create configuration object from runtime_config functions
    return SimpleNamespace(
        config_dir=runtime_config.get_config_dir(),
        namespace=runtime_config.get_namespace(),
        platform=runtime_config.get_platform_config(),
        model_key=runtime_config.get_model_key(),
        model=runtime_config.get_model(),
        scheduler_profile_key=runtime_config.get_scheduler_profile_key(),
        scheduler_profile=runtime_config.get_scheduler_profile(),
        model_cache=runtime_config.get_model_cache_config(),
        smoke_request=runtime_config.get_smoke_request(),
        benchmark=runtime_config.get_benchmark_config(),
    )


def run_prepare_phase() -> int:
    # Initialize configuration first
    init()

    return run_prepare_sequence()


def run_test_phase() -> int:
    config = load_runtime_configuration()
    return test_toolbox_run(
        config_dir=str(config.config_dir),
        namespace=config.namespace,
        inference_service=config.platform["inference_service"],
        gateway=config.platform["gateway"],
        model_key=config.model_key,
        model=config.model,
        scheduler_profile_key=config.scheduler_profile_key,
        scheduler_profile=config.scheduler_profile,
        model_cache=config.model_cache,
        smoke=config.platform["smoke"],
        smoke_request=config.smoke_request,
        benchmark=config.benchmark,
        capture_namespace_events=config.platform["artifacts"]["capture_namespace_events"],
    )


def run_cleanup_phase() -> int:
    config = load_runtime_configuration()
    return cleanup_toolbox_run(
        namespace=config.namespace,
        inference_service_name=config.platform["inference_service"]["name"],
        cleanup_timeout_seconds=config.platform["cluster"]["cleanup_timeout_seconds"],
        benchmark_name=config.benchmark["job_name"] if config.benchmark else None,
    )


def list_vaults() -> list[str]:
    """List all mandatory vaults (excludes optional vaults)."""

    vault_config = config.project.get_config("vaults")

    # Handle both old format (list) and new format (dict with categories)
    if isinstance(vault_config, list):
        return vault_config

    # New format: collect only mandatory vaults (exclude *-optional categories)
    mandatory_vaults = []
    for category, vaults in vault_config.items():
        if isinstance(vaults, list) and not category.endswith("-optional"):
            mandatory_vaults.extend(vaults)

    # Remove duplicates while preserving order
    seen = set()
    unique_vaults = []
    for _vault in mandatory_vaults:
        if _vault in seen:
            continue

        seen.add(_vault)
        unique_vaults.append(_vault)

    return unique_vaults


def get_vaults_for_phase(phase: str) -> list[str]:
    """Get vaults needed for a specific phase.

    Args:
        phase: Phase name ('resolve-only', 'test', 'prepare', 'all')

    Returns:
        List of vault names for the specified phase
    """

    # Get vaults for specific phase, defaulting to empty list if phase doesn't exist
    return config.project.get_config(f"vaults.{phase}", [])


def init_vaults_for_phase(phase: str) -> None:
    """Initialize vaults for a specific phase."""

    # Get global mandatory vaults (always loaded)
    global_mandatory = get_vaults_for_phase("all")

    # Get phase-specific mandatory vaults
    phase_mandatory = get_vaults_for_phase(phase)

    # Combine all mandatory vaults
    mandatory_vaults = global_mandatory + phase_mandatory

    # Get global optional vaults (always loaded optionally)
    global_optional = get_vaults_for_phase("all-optional")

    # Get phase-specific optional vaults
    phase_optional = get_vaults_for_phase(f"{phase}-optional")

    # Combine all optional vaults
    optional_vaults = global_optional + phase_optional

    if not mandatory_vaults and not optional_vaults:
        logger.info(f"No vault to initialize for phase '{phase}'")
        return

    # Initialize both mandatory and optional vaults in a single call
    # Mandatory vaults: strict=True (automation fails if missing/invalid)
    # Optional vaults: strict=False (automation continues with warnings if missing/invalid)
    vault.init(mandatory_vaults=mandatory_vaults, optional_vaults=optional_vaults)


@click.group()
@click.pass_context
@ci_lib.safe_ci_function
def main(ctx):
    """LLM-D Project CI Operations for FORGE."""
    ctx.ensure_object(types.SimpleNamespace)
    init()

    if ctx.invoked_subcommand == "resolve-fournos-config":
        logger.info("No need to initialize the vaults for the resolve step")
        return

    init_vaults_for_phase(ctx.invoked_subcommand)


@main.command()
@click.pass_context
@ci_lib.safe_ci_command
def prepare(ctx) -> int:
    """Prepare phase - Set up environment and dependencies."""
    return run_prepare_phase()


@main.command()
@click.pass_context
@ci_lib.safe_ci_command
def test(ctx) -> int:
    """Test phase - Execute the main testing logic."""
    return run_test_phase()


@main.command()
@click.pass_context
@ci_lib.safe_ci_command
def pre_cleanup(ctx) -> int:
    """Cleanup phase - Clean up resources and finalize."""
    # Cleanup doesn't typically need vaults, but initialize resolve-only for consistency
    # no vault needed
    return run_cleanup_phase()


def list_resolve_vaults() -> list[str]:
    """List all vaults for resolve operations (includes both mandatory and optional)."""

    vault_config = config.project.get_config("vaults")

    # Handle both old format (list) and new format (dict with categories)
    if isinstance(vault_config, list):
        return vault_config

    # New format: collect all vaults from all categories for resolve operations
    all_vaults = []
    for _category, vaults in vault_config.items():
        if isinstance(vaults, list):
            all_vaults.extend(vaults)

    # Remove duplicates while preserving order
    seen = set()
    unique_vaults = []
    for _vault in all_vaults:
        if _vault in seen:
            continue

        seen.add(_vault)
        unique_vaults.append(_vault)

    return unique_vaults


main.add_command(create_fournos_resolve_command(vault_list_func=list_resolve_vaults))
main.add_command(caliper_export_command)


if __name__ == "__main__":
    main()
