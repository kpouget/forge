#!/usr/bin/env python3

import types

import click
import prepare_rhaiis
import test_rhaiis

from projects.core.library import ci as ci_lib


@click.group()
@click.pass_context
@ci_lib.safe_ci_function
def main(ctx):
    """RHAIIS Project CI Operations for FORGE."""
    ctx.ensure_object(types.SimpleNamespace)
    test_rhaiis.init()


@main.command()
@click.pass_context
@ci_lib.safe_ci_command
def prepare(ctx):
    """Prepare phase - Set up environment and dependencies."""
    return prepare_rhaiis.prepare()


@main.command()
@click.pass_context
@ci_lib.safe_ci_command
def test(ctx):
    """Test phase - Deploy model, run benchmarks, capture results."""
    return test_rhaiis.test()


@main.command()
@click.pass_context
@ci_lib.safe_ci_command
def pre_cleanup(ctx):
    """Cleanup phase - Clean up resources and finalize."""
    return prepare_rhaiis.cleanup()


if __name__ == "__main__":
    main()
