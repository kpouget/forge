#!/usr/bin/env python3

import logging
import os
import types

import click

from projects.core.library.cli import safe_cli_command
from projects.llm_d.orchestration import configuration as llmd_configuration
from projects.llm_d.orchestration.cleanup_phase import run as cleanup_toolbox_run
from projects.llm_d.orchestration.prepare_sequence import run_prepare_sequence
from projects.llm_d.orchestration.test_phase import run as test_toolbox_run
from projects.llm_d.runtime import llmd_runtime

logger = logging.getLogger(__name__)


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
    return run_prepare_sequence(
        artifact_dir=config.artifact_dir,
        config_dir=str(config.config_dir),
        namespace=config.namespace,
        namespace_is_managed=config.namespace_is_managed,
        platform=config.platform,
        model_key=config.model_key,
        model=config.model,
        model_cache=config.model_cache,
        benchmark=config.benchmark,
    )


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


@click.group()
@click.pass_context
def main(ctx):
    """LLM-D Project CLI Operations for FORGE."""
    ctx.ensure_object(types.SimpleNamespace)
    init_runtime()


@main.command()
@click.pass_context
@safe_cli_command
def prepare(ctx) -> int:
    """Prepare phase - Set up environment and dependencies."""
    return run_prepare_phase()


@main.command()
@click.pass_context
@safe_cli_command
def test(ctx) -> int:
    """Test phase - Execute the main testing logic."""
    return run_test_phase()


@main.command()
@click.pass_context
@safe_cli_command
def pre_cleanup(ctx) -> int:
    """Cleanup phase - Clean up resources and finalize."""
    return run_cleanup_phase()


@main.command()
@click.pass_context
@safe_cli_command
def post_cleanup(ctx) -> int:
    """Cleanup phase - Clean up resources and finalize."""
    return run_cleanup_phase()


if __name__ == "__main__":
    main()
