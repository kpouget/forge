"""
Shared Caliper "artifacts export" CLI for FORGE project orchestration.

Registers a :mod:`click` subcommand that reads ``caliper`` from project config and runs
:func:`projects.caliper.orchestration.export.run_from_orchestration_config`.
"""

from __future__ import annotations

import logging
import os
from enum import StrEnum
from pathlib import Path
from typing import Any

import click
import yaml

from projects.caliper.orchestration.export import run_from_orchestration_config
from projects.core.library import ci as ci_lib
from projects.core.library import config, env, run

logger = logging.getLogger(__name__)


class FinishReason(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    OTHER = "other"


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


def send_notification(status: dict[str, Any]) -> None:
    """Send job completion notifications based on caliper export status.

    Args:
        status: Caliper export status object containing backend results and metadata
    """
    # Extract notification parameters from status object
    project = _extract_project_from_status(status)
    operation = _extract_operation_from_status(status)
    finish_reason = _extract_finish_reason_from_status(status)
    duration_str = _extract_duration_from_status(status)

    # Apply minimal filtering logic
    if _should_skip_notification(project, operation, finish_reason):
        logger.info(f"Skipping notification for {project} {operation}")
        return

    # Send actual notifications
    logger.info(f"Sending notification: {project} {operation} {finish_reason}{duration_str}")

    # Build enhanced notification with fournos job info and artifact links
    notification_status = _build_enhanced_notification(project, finish_reason, duration_str, status)

    # Write notification to file for GitHub pickup
    try:
        if env.ARTIFACT_DIR:
            notification_file = Path(env.ARTIFACT_DIR) / "NOTIFICATION.html"
            with open(notification_file, "w", encoding="utf-8") as f:
                f.write(notification_status)
            logger.info("Wrote export notification file")
        else:
            logger.warning("ARTIFACT_DIR not available, skipping notification file")
    except Exception as e:
        logger.warning(f"Failed to write notification file: {e}")

    # Actually send notification through GitHub API
    try:
        from projects.core.notifications.send import send_notification as send_github_notification

        success = send_github_notification(
            message=notification_status, github=True, slack=False, dry_run=False
        )
        if success:
            logger.info("Successfully sent GitHub notification")
        else:
            logger.warning("GitHub notification sending failed")
    except Exception as e:
        logger.warning(f"Failed to send GitHub notification: {e}")


def _build_enhanced_notification(
    project: str, finish_reason: FinishReason, duration_str: str, status: dict[str, Any]
) -> str:
    """Build enhanced notification with fournos job config and artifact links."""

    # Get project and args from fournos job or fallback to config
    fjob_project = project
    fjob_args_str = ""

    try:
        if env.BASE_ARTIFACT_DIR:
            fournos_fjob_path = (
                Path(env.BASE_ARTIFACT_DIR) / "000__ci_metadata" / "fournos_fjob.yaml"
            )
            if fournos_fjob_path.exists():
                with open(fournos_fjob_path, encoding="utf-8") as f:
                    fjob_data = yaml.safe_load(f)

                # Extract project and args from fournos job display name if available
                display_name = fjob_data.get("spec", {}).get("displayName", "")
                if display_name:
                    # Display name format is typically "project args"
                    parts = display_name.split()
                    if parts:
                        fjob_project = parts[0]
                        fjob_args_str = " ".join(parts[1:]) if len(parts) > 1 else ""
    except Exception as e:
        logger.warning(f"Failed to read fournos job for project/args: {e}")

    # Fallback to config if fournos job didn't provide values
    if not fjob_args_str:
        try:
            from projects.core.library import config

            job_args = config.project.get_config("ci_job.args", default=[])
            fjob_args_str = " ".join(job_args) if job_args else ""
        except Exception:
            fjob_args_str = ""

    # Base status message with proper formatting
    status_emoji = "🟢" if finish_reason == FinishReason.SUCCESS else "🔴"
    base_status = f"{status_emoji} Testing of `{fjob_project}` {fjob_args_str} {status_emoji}"

    notification_parts = [base_status]

    # 1. Read fournos job execution engine config
    try:
        if env.BASE_ARTIFACT_DIR:
            fournos_fjob_path = (
                Path(env.BASE_ARTIFACT_DIR) / "000__ci_metadata" / "fournos_fjob.yaml"
            )
            if fournos_fjob_path.exists():
                with open(fournos_fjob_path, encoding="utf-8") as f:
                    fjob_data = yaml.safe_load(f)

                execution_engine = fjob_data.get("spec", {}).get("executionEngine", {})
                if execution_engine:
                    # Format execution engine as code block
                    engine_yaml = yaml.dump(
                        execution_engine, default_flow_style=False, sort_keys=True
                    )
                    notification_parts.append("**Execution Engine Configuration:**")
                    notification_parts.append(f"```yaml\n{engine_yaml.strip()}\n```")
    except Exception as e:
        logger.warning(f"Failed to read fournos job config: {e}")

    # 2. Extract artifact links from caliper status
    try:
        artifact_links = []
        step_log_links = []
        mlflow_run_url = None

        # Look for backend results with URLs or artifact references in caliper_artifacts_export
        caliper_export = status.get("caliper_artifacts_export", {})
        backends = caliper_export.get("backends", {})
        for backend_name, backend_result in backends.items():
            if isinstance(backend_result, dict):
                # Check for MLflow experiment_url
                if backend_result.get("experiment_url"):
                    artifact_links.append(
                        f"[{backend_name} Experiment]({backend_result['experiment_url']})"
                    )
                # Check for run_url - store it for step log links
                if backend_result.get("run_url"):
                    mlflow_run_url = backend_result["run_url"]
                    artifact_links.append(f"[{backend_name} Results]({mlflow_run_url})")
                # Check for other URLs
                elif backend_result.get("artifact_url"):
                    artifact_links.append(
                        f"[{backend_name} Artifacts]({backend_result['artifact_url']})"
                    )
                elif backend_result.get("dashboard_url"):
                    artifact_links.append(
                        f"[{backend_name} Dashboard]({backend_result['dashboard_url']})"
                    )

        # Look for general artifact paths or URLs in status
        if status.get("artifact_url"):
            artifact_links.append(f"[Artifacts]({status['artifact_url']})")

        # Browse parent directory for other step run.log files and link them to MLflow
        if not env.BASE_ARTIFACT_DIR:
            logging.error("BASE_ARTIFACT_DIR not set set, cannot generate the notification")
            return "(couldn't generate the notification)"
        if not mlflow_run_url:
            logging.error("mlflow_run_url not set set, cannot generate the notification")
            return "(couldn't generate the notification)"

        parent_dir = Path(env.BASE_ARTIFACT_DIR).parent
        current_step_name = Path(env.BASE_ARTIFACT_DIR).name

        for step_dir in parent_dir.iterdir():
            if not step_dir.is_dir():
                continue

            if step_dir.name == current_step_name:
                continue

            if step_dir.name.startswith("."):
                continue

            run_log = step_dir / "run.log"

            # Guard: Skip if run.log doesn't exist
            if not run_log.exists():
                continue

            # Create MLflow URL with proper artifact path structure
            try:
                # Parse mlflow_run_url to extract base and workspace parameter
                # Expected format: https://mlflow.../artifacts?workspace=...
                if "/artifacts" in mlflow_run_url:
                    base_url, params = mlflow_run_url.split("/artifacts", 1)

                    # Extract workspace parameter if present
                    workspace_param = ""
                    if "?workspace=" in params:
                        workspace_param = params  # Keep the ?workspace=... part

                    # Construct proper MLflow artifact URL
                    mlflow_log_url = (
                        f"{base_url}/artifacts/{step_dir.name}/run.log{workspace_param}"
                    )
                    step_name = step_dir.name.replace("__", " ").replace("_", " ").title()
                    step_log_links.append(f"[{step_name} Log]({mlflow_log_url})")

                    notifications_dir = step_dir / "000__ci_metadata" / "notifications"

                    if not (notifications_dir.exists() and notifications_dir.is_dir()) :
                        continue


                    for notification_file in sorted(notifications_dir.glob("*.txt")):
                        try:
                            with open(notification_file, 'r', encoding='utf-8') as f:
                                content = f.read().strip()

                            if not content:
                                continue

                            # Create subtitle from filename (remove extension, clean up formatting)
                            subtitle = notification_file.stem.replace("__", " ").replace("_", " ").title()
                            step_log_links.append(f"**{subtitle}:**")

                            # Add content with '>' prefix for each line
                            for line in content.splitlines():
                                step_log_links.append(f"> {line}")

                        except Exception as file_error:
                            logger.warning(f"Failed to read notification file {notification_file}: {file_error}")
                            continue
                else:
                    logger.warning(f"Unexpected MLflow URL format: {mlflow_run_url}")
                    continue
            except Exception as e:
                logger.warning(f"Failed to create MLflow link for {run_log}: {e}")
                continue

        # Add artifact links section
        if artifact_links:
            notification_parts.append("**Artifact Links:**")
            notification_parts.extend([f"* {link}" for link in artifact_links])
        else:
            notification_parts.append("**Artifact Links:** No direct links available")

        # Add test logs section
        if step_log_links:
            notification_parts.append("**Test Logs:**")
            notification_parts.extend([f"* {link}" for link in step_log_links])

    except Exception as e:
        logger.warning(f"Failed to extract artifact links: {e}")
        notification_parts.append("**Artifact Links:** Error extracting links")

    return "\n\n".join(notification_parts)


def _extract_project_from_status(status: dict[str, Any]) -> str:
    """Extract project name from status object or environment."""
    # Try to get project from environment variables
    project = os.environ.get("PROJECT_NAME")
    if project:
        return project

    # Fallback to JOB_NAME parsing (common in CI environments)
    job_name = os.environ.get("JOB_NAME", "")
    if job_name and "-" in job_name:
        # Extract project from job name pattern like "project-operation-variant"
        return job_name.split("-")[0]

    return "unknown"


def _extract_operation_from_status(status: dict[str, Any]) -> str:
    """Extract operation name from status object."""
    return "export-artifacts"


def _extract_finish_reason_from_status(status: dict[str, Any]) -> FinishReason:
    """Extract finish reason from status object."""
    # Check if any backend failed in the status
    if not status:
        return FinishReason.ERROR

    # Look for backend results
    backends = status.get("backends", {})
    for backend_name, backend_result in backends.items():
        # Check both explicit success flag and status field
        if backend_result.get("success") is False or backend_result.get("status") not in (
            None,
            "success",
        ):
            logger.info(f"Backend {backend_name} failed, marking as error")
            return FinishReason.ERROR

    return FinishReason.SUCCESS


def _extract_duration_from_status(status: dict[str, Any]) -> str:
    """Extract duration from status object."""
    # Look for duration in status
    duration = status.get("duration")
    if duration:
        return f" after {duration}"
    return ""


def _should_skip_notification(project: str, operation: str, finish_reason: FinishReason) -> bool:
    """Apply minimal filtering logic to determine if notification should be skipped."""
    # Minimal filtering - no special cases for now
    return False


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

    status = None
    try:
        status = run_caliper_orchestration_export(artifact_directory=artifact_directory)
        logger.info("Export status:\n" + yaml.dump(status, indent=4))

        # Update fjob status with export results
        _update_fjob_export_status(status)

    except Exception as e:
        logger.error(f"Export failed: {e}")
        # Create failure status for notification
        status = {"success": False, "error": str(e), "backends": {}}
        raise  # Re-raise to maintain error behavior

    finally:
        # Send completion notifications regardless of success/failure
        if status:
            try:
                send_notification(status)
            except Exception as e:
                logger.warning(f"Failed to send notifications: {e}")
                # Don't fail the entire job if notifications fail

    return 0
