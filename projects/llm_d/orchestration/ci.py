#!/usr/bin/env python3
"""
LLM-D Project CI Operations

"""

import os
import types

import click

from projects.core.library import ci as ci_lib
from projects.llm_d.orchestration import configuration as llmd_configuration
from projects.llm_d.orchestration.prepare_sequence import run_prepare_sequence
from projects.llm_d.runtime import llmd_runtime, phase_inputs
from projects.llm_d.toolbox.cleanup.main import run as cleanup_toolbox_run
from projects.llm_d.toolbox.test.main import run as test_toolbox_run


def init_runtime() -> None:
    llmd_runtime.init()


def load_runtime_configuration(*, cwd=None, artifact_dir=None):
    kwargs = {
        "requested_preset": os.environ.get("FORGE_PRESET"),
        "job_name": os.environ.get("FORGE_JOB_NAME"),
    }
    if cwd is not None:
        kwargs["cwd"] = cwd
    if artifact_dir is not None:
        kwargs["artifact_dir"] = artifact_dir

    return llmd_configuration.load_runtime_configuration(**kwargs)


def run_prepare_phase() -> int:
    config = load_runtime_configuration()
    return run_prepare_sequence(config)


def run_test_phase() -> int:
    config = load_runtime_configuration()
    inputs_file = phase_inputs.write_test_inputs(config)
    return test_toolbox_run(inputs_file=str(inputs_file))


def run_cleanup_phase() -> int:
    config = load_runtime_configuration()
    inputs_file = phase_inputs.write_cleanup_inputs(config)
    return cleanup_toolbox_run(inputs_file=str(inputs_file))


@click.group()
@click.pass_context
@ci_lib.safe_ci_function
def main(ctx):
    """LLM-D Project CI Operations for FORGE."""
    ctx.ensure_object(types.SimpleNamespace)
    init_runtime()


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
    return run_cleanup_phase()


if __name__ == "__main__":
    main()
