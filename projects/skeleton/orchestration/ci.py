#!/usr/bin/env python3
"""
Skeleton Example Project CI Operations

This is a skeleton/template project that demonstrates how to create a new project
within the FORGE test harness framework. Use this as a starting point for
building your own projects.
"""

import logging
import types
from pathlib import Path

import click
import prepare_skeleton
import test_skeleton

from projects.core.ci_entrypoint.fournos_resolve import create_fournos_resolve_entrypoint
from projects.core.library import ci as ci_lib
from projects.core.library import config, env, run, vault
from projects.core.library.export import caliper_export_entrypoint
from projects.core.library.replot import caliper_replot_entrypoint

logger = logging.getLogger(__name__)


def init():
    """Initialize Skeleton orchestration environment"""
    env.init()
    run.init()
    config.init(Path(__file__).parent)


@click.group(cls=ci_lib.HelpfulGroup)
@click.pass_context
@ci_lib.safe_ci_function
def main(ctx):
    """Skeleton example project CI operations for FORGE."""
    ctx.ensure_object(types.SimpleNamespace)
    init()

    if ctx.invoked_subcommand == "resolve-fournos-config":
        logger.info("No need to initialize the vaults for the resolve step")
        return

    vault.phase_vault_init(ctx.invoked_subcommand)


@main.command()
@click.pass_context
@ci_lib.safe_ci_command
def prepare(ctx):
    """Prepare phase - Set up environment and dependencies."""
    return prepare_skeleton.prepare()


@main.command()
@click.pass_context
@ci_lib.safe_ci_command
def test(ctx):
    """Test phase - Execute the main testing logic."""
    return test_skeleton.test()


@main.command()
@click.pass_context
@ci_lib.safe_ci_command
def pre_cleanup(ctx):
    """Cleanup phase - Clean up resources and finalize."""
    return prepare_skeleton.cleanup()


@main.command()
@click.pass_context
@ci_lib.safe_ci_command
def post_cleanup(ctx):
    """Cleanup phase - Clean up resources and finalize."""
    return prepare_skeleton.cleanup()


main.add_command(caliper_export_entrypoint)
main.add_command(caliper_replot_entrypoint)

main.add_command(
    create_fournos_resolve_entrypoint(
        vault_list_func=vault.phase_vault_list_all,
        hardware_resolver_func=test_skeleton.resolve_hardware_request,
    )
)


if __name__ == "__main__":
    main()
