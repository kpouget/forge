#!/usr/bin/env python3
"""
Skeleton Project CLI entrypoint
"""

import logging
import sys
import types

import click
import prepare_skeleton
import test_skeleton

from projects.core.library import config
from projects.core.library.cli import safe_cli_command
from projects.core.library.postprocess import postprocess_command

logger = logging.getLogger(__name__)


@click.group()
@click.option("--presets", multiple=True, help="Apply presets to the configuration")
@click.pass_context
def main(ctx, presets):
    """CLI Operations."""
    ctx.ensure_object(types.SimpleNamespace)
    test_skeleton.init(strict_vault_validation=False)

    # Apply presets if provided
    if not presets:
        return

    try:
        for preset_name in presets:
            logger.info(f"Applying preset: {preset_name}")
            config.project.apply_preset(preset_name)
    except ValueError as e:
        logger.error(f"Failed to apply preset '{preset_name}': {e}")
        sys.exit(1)


@main.command()
@click.pass_context
@safe_cli_command
def prepare(ctx):
    """Prepare phase - Set up environment and dependencies."""
    exit_code = prepare_skeleton.prepare()
    sys.exit(exit_code)


@main.command()
@click.pass_context
@safe_cli_command
def test(ctx):
    """Test phase - Execute the main testing logic."""
    exit_code = test_skeleton.test()
    sys.exit(exit_code)


@main.command()
@click.pass_context
@safe_cli_command
def cleanup(ctx):
    """Cleanup phase - Clean up resources and finalize."""
    exit_code = prepare_skeleton.cleanup()
    sys.exit(exit_code)


main.add_command(postprocess_command)


if __name__ == "__main__":
    main()
