#!/usr/bin/env python3
"""
LLM-D Project CI Operations

"""

import logging
import types
from pathlib import Path

import click

from projects.core.ci_entrypoint.fournos_resolve import create_fournos_resolve_command

# Use core K8s utilities instead of llmd_runtime
from projects.core.library import ci as ci_lib
from projects.core.library import config, env, run, vault
from projects.core.library.export import caliper_export_command
from projects.llm_d.orchestration.cleanup_phase import run as cleanup_toolbox_run
from projects.llm_d.orchestration.prepare_sequence import run_prepare_sequence
from projects.llm_d.orchestration.test_phase import run as test_toolbox_run

logger = logging.getLogger(__name__)


def init():
    """Initialize LLM-D orchestration environment"""
    env.init()
    run.init()
    config.init(Path(__file__).parent)


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


@click.group(cls=ci_lib.HelpfulGroup)
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
    return run_prepare_sequence()


@main.command()
@click.pass_context
@ci_lib.safe_ci_command
def test(ctx) -> int:
    """Test phase - Execute the main testing logic."""
    return test_toolbox_run()


@main.command()
@click.pass_context
@ci_lib.safe_ci_command
def pre_cleanup(ctx) -> int:
    """Cleanup phase - Clean up resources and finalize."""
    # Cleanup doesn't typically need vaults, but initialize resolve-only for consistency
    # no vault needed
    from projects.llm_d.orchestration import runtime_config

    namespace = runtime_config.get_namespace()
    return cleanup_toolbox_run(namespace=namespace)


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
