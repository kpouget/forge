"""
Shared Caliper parse / visualize orchestration for FORGE projects.

Registers a :mod:`click` subcommand that reads ``caliper.postprocess`` from project config and runs
:func:`projects.caliper.orchestration.postprocess.run_postprocess_from_orchestration_config`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import click
import yaml

from projects.caliper.orchestration.postprocess import (
    run_postprocess_from_orchestration_config,
)
from projects.caliper.orchestration.postprocess_outcome import TestPhaseOutcome
from projects.core.library import ci as ci_lib
from projects.core.library import config, env
from projects.core.library.reports_index import generate_caliper_reports_index

logger = logging.getLogger(__name__)


def run_and_postprocess(test_func, *args, **kwargs):
    """
    Wrapper that runs a test function and handles outcome tracking with Caliper postprocessing.

    This wrapper:
    1. Executes the provided test function with given arguments
    2. Captures test outcomes (success, failure, exception details)
    3. Runs Caliper postprocessing with the test outcome
    4. Properly chains exceptions if both test and postprocess fail

    Args:
        test_func: Callable test function to execute
        *args: Positional arguments to pass to test_func
        **kwargs: Keyword arguments to pass to test_func

    Returns:
        The return value from the test function

    Raises:
        The original exception from test_func, or postprocess exception if test succeeds
        but postprocessing fails. If both fail, exceptions are properly chained.
    """
    artifact_base_dir = Path(env.ARTIFACT_DIR).resolve()

    exc_msg: str | None = None
    ret: int | None = None
    original_exc: BaseException | None = None  # Store the test exception

    try:
        ret = test_func(*args, **kwargs)
        return ret
    except BaseException as e:
        exc_msg = str(e)
        original_exc = e  # Capture before the name is cleared
        raise
    finally:
        # Determine test outcome based on exception/return code
        if exc_msg is not None:
            outcome = TestPhaseOutcome("FAILED", exc_msg)
        elif ret == 0:
            outcome = TestPhaseOutcome("SUCCESS")
        elif ret is None:
            outcome = TestPhaseOutcome("FAILED", "test aborted without exit code")
        else:
            outcome = TestPhaseOutcome("FAILED", f"exit_code={ret}")

        # Run postprocessing and handle potential failures
        try:
            run_caliper_postprocess_after_test(artifact_base_dir, test_outcome=outcome)
        except Exception as postprocess_exc:
            logger.exception("Caliper postprocess after test failed")
            if original_exc is not None:
                # Both test and postprocess failed: chain so both are visible in the traceback
                raise postprocess_exc from original_exc
            raise  # Only postprocess failed — propagate normally


def run_caliper_postprocess_after_test(
    artifact_root: Path | os.PathLike[str] | str | None,
    *,
    test_outcome: TestPhaseOutcome | None = None,
) -> None:
    """
    Run Caliper post-processing after the orchestration test phase.

    Uses ``artifact_root`` (typically :data:`env.ARTIFACT_BASE_DIR`) as the Caliper artifact tree,
    and :func:`env.NextArtifactDir` ``(\"postprocessing\")`` as the workspace for visualize output,
    KPI JSONL, and regression artifacts.

    ``test_outcome`` feeds ``final_status`` computation together with parse/visualize/KPI outcomes.
    """
    full_cal = config.project.get_config("caliper", print=False)
    post = (full_cal or {}).get("postprocess") or {}
    if not post.get("enabled", True):
        logger.info("Caliper post-processing disabled (caliper.postprocess.enabled: false).")
        return

    artifact_root_path = Path(artifact_root).resolve() if artifact_root is not None else None

    with env.NextArtifactDir("postprocessing"):
        workspace = Path(env.ARTIFACT_DIR).resolve()
        logger.info(
            "Running Caliper postprocess (artifacts=%s, workspace=%s, test_phase=%s)",
            artifact_root_path,
            workspace,
            test_outcome.phase if test_outcome else "SUCCESS",
        )
        status = run_caliper_orchestration_postprocess(
            artifact_directory=artifact_root_path,
            visualize_output_directory=workspace,
            postprocessing_workspace=workspace,
            test_outcome=test_outcome,
        )
        logger.info(
            "Caliper postprocess finished:\n%s",
            yaml.dump(status, indent=2, default_flow_style=False, sort_keys=False),
        )

        # Generate reports index if visualization was successful
        try:
            index_path = generate_caliper_reports_index(status, workspace, "reports_index.html")
            if index_path:
                logger.info("Generated reports index at %s", index_path)
        except Exception as e:
            logger.warning("Failed to generate reports index: %s", e)

    return status


def resolve_caliper_postprocess_artifacts_dir(
    *,
    artifact_directory: Path | None,
    caliper_cfg: dict[str, Any] | None,
) -> Path:
    """
    Resolve the Caliper **artifact tree** root (``__test_labels__.yaml``, manifest).

    Precedence: explicit ``artifact_directory``, ``caliper.postprocess.artifacts_dir``,
    ``ARTIFACT_BASE_DIR``.
    """
    if artifact_directory is not None:
        return artifact_directory.expanduser().resolve()

    root = caliper_cfg or {}
    post = root.get("postprocess") or {}
    raw = post.get("artifacts_dir")
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser().resolve()

    if "ARTIFACT_BASE_DIR" in os.environ:
        return Path(os.environ["ARTIFACT_BASE_DIR"]).expanduser().resolve()

    raise ValueError(
        "Caliper postprocess requires the artifact tree root: use --artifact-directory, "
        "set caliper.postprocess.artifacts_dir in project config, or set ARTIFACT_BASE_DIR."
    )


def orchestration_artifact_dir() -> Path | None:
    """FORGE CI artifact directory ($ARTIFACT_DIR), when initialized."""
    try:
        ad = env.ARTIFACT_DIR
    except Exception:
        return None
    return Path(ad) if ad is not None else None


def run_caliper_orchestration_postprocess(
    *,
    artifact_directory: Path | None,
    visualize_output_directory: Path | None = None,
    postprocessing_workspace: Path | None = None,
    test_outcome: TestPhaseOutcome | None = None,
) -> dict[str, Any]:
    """Load ``caliper`` from project config and run enabled post-processing steps."""
    caliper_cfg = config.project.get_config("caliper", print=False)
    artifacts_dir = resolve_caliper_postprocess_artifacts_dir(
        artifact_directory=artifact_directory,
        caliper_cfg=caliper_cfg,
    )
    result = run_postprocess_from_orchestration_config(
        caliper_cfg,
        artifacts_dir=artifacts_dir,
        orchestration_artifact_dir=orchestration_artifact_dir(),
        visualize_output_directory=visualize_output_directory,
        postprocessing_workspace=postprocessing_workspace,
        test_outcome=test_outcome,
    )

    status_base = (
        postprocessing_workspace or visualize_output_directory or orchestration_artifact_dir()
    )
    if status_base is not None:
        status_path = Path(status_base) / "caliper_postprocess_status.yaml"
        try:
            status_path.parent.mkdir(parents=True, exist_ok=True)
            status_path.write_text(
                yaml.dump(result, indent=2, default_flow_style=False, sort_keys=False),
                encoding="utf-8",
            )
            logger.info("Wrote postprocess status YAML to %s", status_path)
        except OSError as e:
            logger.warning("Could not write %s: %s", status_path, e)

    return result


@click.command("postprocess")
@click.option(
    "--artifact-directory",
    "artifact_directory",
    type=click.Path(path_type=Path, exists=True, file_okay=False, dir_okay=True),
    default=None,
    help=(
        "Caliper artifact tree root (directories with __test_labels__.yaml). "
        "Overrides caliper.postprocess.artifacts_dir and ARTIFACT_BASE_DIR when set."
    ),
)
@click.option(
    "--output-directory",
    "output_directory",
    type=click.Path(path_type=Path, exists=True, file_okay=False, dir_okay=True),
    default=None,
    help=(
        "Output directory, where the post processing results will be stored. "
        "Overrides caliper.postprocess.artifacts_dir and ARTIFACT_BASE_DIR when set."
    ),
)
@click.pass_context
@ci_lib.safe_ci_command
def caliper_postprocess_command(_ctx, artifact_directory: Path | None, output_directory: Path | None):
    """Run Caliper parse / visualize / KPI pipeline from ``caliper.postprocess``."""

    status = run_caliper_orchestration_postprocess(
        artifact_directory=artifact_directory,
        test_outcome=TestPhaseOutcome("SUCCESS"),
        visualize_output_directory=output_directory,
    )
    logger.info("Caliper postprocess status:\n" + yaml.dump(status, indent=2))

    # Generate reports index if output directory is specified
    if output_dir:
        try:
            index_path = generate_caliper_reports_index(status, output_dir, "reports_index.html")
            if index_path:
                logger.info("Generated reports index at %s", index_path)
        except Exception as e:
            logger.warning("Failed to generate reports index: %s", e)

    return 0
