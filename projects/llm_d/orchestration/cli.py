#!/usr/bin/env python3

import logging
import types

import click
import prepare_llmd
import test_llmd

from projects.core.library.cli import safe_cli_command

logger = logging.getLogger(__name__)


@click.group()
@click.pass_context
def main(ctx):
    """LLM-D Project CLI Operations for FORGE."""
    ctx.ensure_object(types.SimpleNamespace)
    test_llmd.init()


@main.command()
@click.pass_context
@safe_cli_command
def prepare(ctx) -> int:
    """Prepare phase - Set up environment and dependencies."""
    return prepare_llmd.prepare()


@main.command()
@click.pass_context
@safe_cli_command
def test(ctx) -> int:
    """Test phase - Execute the main testing logic."""
    return test_llmd.test()


@main.command()
@click.pass_context
@safe_cli_command
def pre_cleanup(ctx) -> int:
    """Cleanup phase - Clean up resources and finalize."""
    return prepare_llmd.cleanup()


@main.command()
@click.pass_context
@safe_cli_command
def post_cleanup(ctx) -> int:
    """Cleanup phase - Clean up resources and finalize."""
    return prepare_llmd.cleanup()


if __name__ == "__main__":
    main()
