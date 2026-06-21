"""
Base CI app factory for FORGE projects.

Eliminates repetitive setup by letting each project declare its phases
as a simple dict of {command_name: "dotted.module.path:function"}.
"""

from __future__ import annotations

import importlib
import logging
import types
from collections.abc import Callable
from pathlib import Path

import click

from projects.core.ci_entrypoint.fournos_resolve import create_fournos_resolve_entrypoint
from projects.core.library import ci as ci_lib
from projects.core.library import config, env, run, vault
from projects.core.library.export import caliper_export_entrypoint

logger = logging.getLogger(__name__)


def _import_function(dotted_path: str) -> Callable:
    """
    Import a function from a dotted path like 'projects.foo.bar:my_func'.
    """
    module_path, func_name = dotted_path.rsplit(":", 1)
    module = importlib.import_module(module_path)
    return getattr(module, func_name)


def create_ci_app(
    *,
    project_name: str,
    description: str,
    config_dir: Path,
    phases: dict[str, str | dict],
    preset_optional_phases: list[str] | None = None,
) -> click.Group:
    """
    Create a fully-wired FORGE CI click app for a project.

    Args:
        project_name: e.g. "mcp_gateway", "llamastack"
        description: Click group help string
        config_dir: Path to the orchestration directory (where config.d/ lives)
        phases: Mapping of command_name → function reference.
                Value is either:
                  - A dotted import path: "projects.foo.phase:run"
                  - A dict with keys: {"func": "dotted.path:fn", "help": "..."}
        preset_optional_phases: List of phase names that don't require FORGE_PRESET.

    Returns:
        A click.Group ready to be called as the CLI entrypoint.
    """
    _preset_optional = set(preset_optional_phases or [])

    def _init(phase: str | None = None):
        import os

        env.init()
        run.init()
        config.init(config_dir)

        requested_preset = os.environ.get("FORGE_PRESET")
        if requested_preset:
            if not config.project.get_preset(requested_preset):
                if phase and phase in _preset_optional:
                    logger.info(
                        "Preset '%s' not found, but not required for '%s'", requested_preset, phase
                    )
                else:
                    raise ValueError(f"Unknown {project_name} preset: {requested_preset}")
            else:
                config.project.apply_preset(requested_preset)
                config.project.apply_config_overrides(log=False)

    def _get_vaults_for_phase(phase: str) -> list[str]:
        return config.project.get_config(f"vaults.{phase}", [])

    def _init_vaults_for_phase(phase: str) -> None:
        global_mandatory = _get_vaults_for_phase("all")
        phase_mandatory = _get_vaults_for_phase(phase)
        mandatory_vaults = global_mandatory + phase_mandatory

        global_optional = _get_vaults_for_phase("all-optional")
        phase_optional = _get_vaults_for_phase(f"{phase}-optional")
        optional_vaults = global_optional + phase_optional

        if not mandatory_vaults and not optional_vaults:
            logger.info(f"No vaults to initialize for phase '{phase}'")
            return

        vault.init(mandatory_vaults=mandatory_vaults, optional_vaults=optional_vaults)

    def _list_vaults() -> list[str]:
        vault_config = config.project.get_config("vaults")
        if isinstance(vault_config, list):
            return vault_config
        all_vaults = []
        for _category, vaults in vault_config.items():
            if isinstance(vaults, list):
                all_vaults.extend(vaults)
        seen = set()
        return [v for v in all_vaults if v not in seen and not seen.add(v)]

    @click.group()
    @click.pass_context
    @ci_lib.safe_ci_function
    def main(ctx):
        ctx.ensure_object(types.SimpleNamespace)
        _init(phase=ctx.invoked_subcommand)

        if ctx.invoked_subcommand == "resolve-fournos-config":
            logger.info("No need to initialize the vaults for the resolve step")
            return

        _init_vaults_for_phase(ctx.invoked_subcommand)

    main.help = description

    for cmd_name, phase_spec in phases.items():
        if isinstance(phase_spec, str):
            func_path = phase_spec
            help_text = None
        else:
            func_path = phase_spec["func"]
            help_text = phase_spec.get("help")

        _register_phase_command(main, cmd_name, func_path, help_text)

    main.add_command(create_fournos_resolve_entrypoint(vault_list_func=_list_vaults))
    main.add_command(caliper_export_entrypoint)

    return main


def _register_phase_command(
    group: click.Group,
    cmd_name: str,
    func_path: str,
    help_text: str | None,
) -> None:
    """Register a lazily-imported phase function as a click command."""

    @click.pass_context
    @ci_lib.safe_ci_command
    def command(ctx, _func_path=func_path):
        fn = _import_function(_func_path)
        return fn()

    command.__name__ = cmd_name.replace("-", "_")
    if help_text:
        command.__doc__ = help_text

    group.command(name=cmd_name)(command)
