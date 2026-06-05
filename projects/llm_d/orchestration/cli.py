#!/usr/bin/env python3
"""
Skeleton Project CLI entrypoint
"""

import logging
import sys
import types
from pathlib import Path

import click

from projects.core.library import config, env, run
from projects.core.library.postprocess import postprocess_command

logger = logging.getLogger(__name__)


def init():
    """Initialize LLM-D orchestration environment"""
    env.init()
    run.init()
    config.init(Path(__file__).parent)


@click.group()
@click.option(
    "--preset",
    multiple=True,
    help="Apply a preset to the configuration. Pass multiple --preset NAME to apply multiple presets.",
)
@click.pass_context
def main(ctx, preset):
    """CLI Operations."""
    ctx.ensure_object(types.SimpleNamespace)
    init()

    if not preset:
        return

    try:
        for preset_name in preset:
            logger.info(f"Applying preset: {preset_name}")
            config.project.apply_preset(preset_name)
    except ValueError as e:
        logger.error(f"Failed to apply preset '{preset_name}': {e}")
        sys.exit(1)


main.add_command(postprocess_command)


if __name__ == "__main__":
    main()
