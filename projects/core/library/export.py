"""
Shared Caliper “artifacts export” CLI for FORGE project orchestration.

Registers a :mod:`click` subcommand that reads ``caliper`` from project config and runs
:func:`projects.caliper.orchestration.export.run_from_orchestration_config`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import click
import yaml

from projects.caliper.orchestration.export import run_from_orchestration_config
from projects.core.library import ci as ci_lib
from projects.core.library import config, run

logger = logging.getLogger(__name__)


def _update_fjob_export_status(status: dict):
    """Update FournosJob status with export artifacts status."""
    if os.environ.get("FOURNOS_CI") != "true":
        return

    # Unset KUBECONFIG to use the pod SA access
    original_kubeconfig = os.environ.get("KUBECONFIG")
    if "KUBECONFIG" in os.environ:
        del os.environ["KUBECONFIG"]

    try:
        import json

        fjob_name = os.environ["FJOB_NAME"]
        namespace = os.environ["FOURNOS_WORKLOAD_NAMESPACE"]

        # Get current fjob status
        get_cmd = f"oc get fjob/{fjob_name} -n {namespace} -ojson"
        result = run.run(get_cmd, capture_stdout=True, check=False)

        if result.returncode != 0:
            logger.warning(f"Failed to get fjob/{fjob_name}")
            return

        fjob_data = json.loads(result.stdout)

        # Initialize status.engine.status if it doesn't exist
        if "status" not in fjob_data:
            fjob_data["status"] = {}
        if "engineStatus" not in fjob_data["status"]:
            fjob_data["status"]["engineStatus"] = {}
        if "forge" not in fjob_data["status"]["engineStatus"]:
            fjob_data["status"]["engineStatus"]["forge"] = {}
        if "status" not in fjob_data["status"]["engineStatus"]["forge"]:
            fjob_data["status"]["engineStatus"]["forge"]["status"] = {}

        # Update with export-artifacts status
        fjob_data["status"]["engineStatus"]["forge"]["exportArtifacts"] = status

        # Patch the fjob
        patch_data = {"status": fjob_data["status"]}
        patch_cmd = f"oc patch fjob/{fjob_name} -n {namespace} --type=merge --subresource=status -p '{json.dumps(patch_data)}'"

        patch_result = run.run(patch_cmd, check=False)
        if patch_result.returncode == 0:
            logger.info(f"Updated fjob/{fjob_name} status with export artifacts status")
        else:
            logger.warning(f"Failed to update fjob status: {patch_cmd}")

    except Exception as e:
        logger.warning(f"Failed to update fjob status: {e}")
    finally:
        # Restore KUBECONFIG if it was set
        if original_kubeconfig is not None:
            os.environ["KUBECONFIG"] = original_kubeconfig


def run_caliper_orchestration_export(*, artifact_directory: Path | None):
    """Set optional ``caliper.export.from`` and run orchestration export."""

    if artifact_directory is None and "ARTIFACT_BASE_DIR" in os.environ:
        artifact_directory = os.environ["ARTIFACT_BASE_DIR"]

    if artifact_directory is not None:
        config.project.set_config("caliper.export.from", str(artifact_directory))

    # Use FJOB_NAME as fallback for mlflow run_name if not configured
    run_name = config.project.get_config(
        "caliper.export.backend.mlflow.config.run_name", None, print=False, warn=False
    )
    if run_name is None and "FJOB_NAME" in os.environ:
        config.project.set_config(
            "caliper.export.backend.mlflow.config.run_name", os.environ["FJOB_NAME"], print=False
        )

    caliper_cfg = config.project.get_config("caliper", print=False)

    return run_from_orchestration_config(caliper_cfg)


@click.command("export-artifacts")
@click.option(
    "--artifact-directory",
    "artifact_directory",
    type=click.Path(path_type=Path, exists=False, file_okay=True, dir_okay=True),
    default=None,
    help="If set, overrides caliper.export.from (artifact root directory).",
)
@click.pass_context
@ci_lib.safe_ci_command
def caliper_export_entrypoint(_ctx, artifact_directory: Path | None):
    """Export the file artifacts."""

    status = run_caliper_orchestration_export(artifact_directory=artifact_directory)
    logger.info("Export status:\n" + yaml.dump(status, indent=4))

    # Update fjob status with export results
    _update_fjob_export_status(status)

    return 0
