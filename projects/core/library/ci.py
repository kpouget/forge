#!/usr/bin/env python3
"""
Shared CI utilities for FORGE projects

Provides common CI functionality including error handling, logging,
and tooling setup for consistent behavior across all projects.
"""

import functools
import logging
import sys
import traceback

import click

from projects.core.dsl import toolbox as dsl_toolbox
from projects.core.dsl.runtime import TaskExecutionError
from projects.core.library import env

logger = logging.getLogger(__name__)


class HelpfulGroup(click.Group):
    """
    A Click group that automatically shows help when a command is not found.
    """

    def get_command(self, ctx, cmd_name):
        rv = click.Group.get_command(self, ctx, cmd_name)
        if rv is not None:
            return rv

        # Command not found - show help
        print(f"Error: No such command '{cmd_name}'.\n", file=sys.stderr)
        print(ctx.get_help(), file=sys.stderr)
        ctx.exit(2)


def handle_ci_exception(e: Exception) -> None:
    """
    Handle CI exceptions with comprehensive logging and failure file creation.

    Args:
        e: The exception that occurred
    """

    # Display error on screen and write to FAILURES file
    _display_error_summary(e)


def _display_error_summary(e: Exception) -> None:
    """Display a comprehensive error summary on screen and write to FAILURES file."""

    # Build the error summary as a list of lines
    logger.error("")
    logger.error("=" * 80)
    logger.error("🚨 CI EXECUTION FAILED")
    logger.error("=" * 80)
    logger.error("")

    summary_lines = []
    if isinstance(e, TaskExecutionError):
        summary_lines += dsl_toolbox.get_task_execution_error(e)
        summary_lines.append("")
        summary_lines.append("---")

    # mind that any thing below a '---\n' will be cut in the notification

    # Add the full stacktrace
    summary_lines.append(f"--- 📍{e.__class__.__name__} STACKTRACE ---")
    summary_lines.append(f"--- 📍{str(e)}")
    summary_lines.append("")

    full_traceback = traceback.format_exc().splitlines()
    # Add each line of the stacktrace with proper indentation
    for line in full_traceback:
        summary_lines.append(f"   {line}")

    if not isinstance(e, TaskExecutionError):
        summary_lines.append("---")  # add the details marker after the stacktrace

    # Display on screen
    for line in summary_lines:
        logger.error(line)

    if env.ARTIFACT_DIR is None:
        logging.error("env.ARTIFACT_DIR not set, cannot generate the error summary file")
    else:
        # Write to FAILURES file
        _write_error_summary_to_file(summary_lines)

    logger.error("=" * 80)


def _write_error_summary_to_file(summary_lines: list) -> None:
    """Write the error summary to FAILURE file."""
    failures_file = env.ARTIFACT_DIR / "FAILURE"

    try:
        content = "\n".join(summary_lines)
        failures_file.write_text(content + "\n")
        logger.info(f"Error summary written to: {failures_file}")
    except Exception as write_error:
        logger.error(f"Failed to write error summary to file: {write_error}")


def safe_ci_command(command_func):
    """
    Decorator/wrapper for CI commands to provide consistent error handling.

    Args:
        command_func: Function to execute safely
    """

    @functools.wraps(command_func)
    def wrapper(*args, **kwargs):
        try:
            exit_code = command_func(*args, **kwargs)
            sys.exit(exit_code)
        except Exception as e:
            handle_ci_exception(e)
            sys.exit(1)

    return wrapper


def safe_ci_function(command_func):
    """
    Decorator/wrapper for CI commands to provide consistent error handling.
    This version does NOT exit on success.

    Args:
        command_func: Function to execute safely
    """

    @functools.wraps(command_func)
    def wrapper(*args, **kwargs):
        try:
            return command_func(*args, **kwargs)
        except Exception as e:
            handle_ci_exception(e)
            sys.exit(1)

    return wrapper
