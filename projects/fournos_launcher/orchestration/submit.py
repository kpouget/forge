import logging
import os
import pathlib
import signal
import sys
import threading
import traceback
from datetime import datetime

from projects.core.dsl.utils.k8s import sanitize_k8s_name
from projects.core.library import config, env, run, vault
from projects.core.library.run_parallel import Parallel
from projects.fournos_launcher.orchestration import job_management, pr_args
from projects.fournos_launcher.toolbox.cleanup_fjob.main import (
    run as cleanup_fjob,
)
from projects.fournos_launcher.toolbox.submit_and_wait.main import (
    run as submit_and_wait,
)

logger = logging.getLogger(__name__)


def _signal_handler_sigint(sig, frame):
    """Handle SIGINT (Ctrl+C) for FOURNOS launcher."""
    print("\n🚫 FOURNOS launcher received SIGINT - shutting down jobs...")
    env.reset_artifact_dir()
    job_management.shutdown_fjobs_on_interrupt()
    sys.exit(137)


def _signal_handler_sigterm(sig, frame):
    """Handle SIGTERM for FOURNOS launcher."""
    print("\n🛑 FOURNOS launcher received SIGTERM - shutting down jobs...")
    env.reset_artifact_dir()
    job_management.shutdown_fjobs_on_interrupt()
    sys.exit(143)


def _setup_signal_handlers():
    """Set up signal handlers for FOURNOS job shutdown."""
    try:
        # Store original handlers
        original_sigint = signal.signal(signal.SIGINT, _signal_handler_sigint)
        original_sigterm = signal.signal(signal.SIGTERM, _signal_handler_sigterm)

        logger.debug("FOURNOS signal handlers installed")

        # Store references so we can restore them if needed
        _setup_signal_handlers._original_sigint = original_sigint
        _setup_signal_handlers._original_sigterm = original_sigterm

    except Exception as e:
        logger.warning(f"Failed to set up FOURNOS signal handlers: {e}")


def init():
    env.init()
    run.init()
    result = pr_args.apply_pr_directives()
    if result == "help":
        logger.info("Help was requested - exiting with code 0")
        exit(0)

    config.init(pathlib.Path(__file__).parent, apply_config_overrides=False)
    config.project.apply_config_overrides(ignore_not_found=True)
    config.project.filter_out_used_overrides()
    vault.init(config.project.get_config("vaults"))

    prepare_env()


def prepare_env():
    kubeconfig_path = vault.get_vault_content_path(
        config.project.get_config("fournos.kubeconfig.vault.name"),
        config.project.get_config("fournos.kubeconfig.vault.key"),
    )

    os.environ["KUBECONFIG"] = str(kubeconfig_path)


def submit_job():
    # Set up signal handlers for graceful job shutdown on interruption
    _setup_signal_handlers()

    overrides = {}
    overrides.update(config.project.get_config("overrides"))
    overrides.update(config.project.get_config("extra_overrides"))

    # Build env dict from pass lists
    env_dict = {}
    env_pass_lists = config.project.get_config("fournos.job.env", print=False)
    for _, pass_list in (env_pass_lists or {}).items():
        for env_var in pass_list:
            if env_var in os.environ:
                env_dict[env_var] = os.environ[env_var]

    # Add extra environment variables
    extra_env = config.project.get_config("fournos.job.extra_env", {}, print=False)
    env_dict.update(extra_env)

    # Update display name with project and args
    project_name = config.project.get_config("ci_job.project")
    job_args = config.project.get_config("ci_job.args")

    # job_args is always a list, format accordingly
    args_str = " ".join(job_args)

    display_name = f"{project_name} {args_str}".strip()
    config.project.set_config("fournos.job.display_name", display_name)
    logger.info(f"Set job display name: {display_name}")

    # Validate required configuration before job submission
    cluster_name = config.project.get_config("cluster.name")
    if not cluster_name:
        raise ValueError(
            "cluster.name must be configured in config.yaml - cannot submit job without target cluster"
        )

    # Get GPU hardware configuration
    gpu_count = config.project.get_config("fournos.job.hardware.gpu_count")
    gpu_type = config.project.get_config("fournos.job.hardware.gpu_type")

    # Validate GPU configuration - both must be present or both must be missing
    gpu_config_present = (gpu_count is not None, gpu_type is not None)
    if gpu_config_present[0] != gpu_config_present[1]:
        raise ValueError(
            "GPU configuration invalid: both gpu_count and gpu_type must be specified together, "
            f"or both must be null. Got gpu_count={gpu_count}, gpu_type={gpu_type}"
        )

    # Check if parallel jobs are configured
    parallel_jobs = config.project.get_config("fournos_launcher.parallel_jobs", {}, print=False)
    parallel_job_configs = []

    for idx, job_args_list in parallel_jobs.items():
        if job_args_list:  # Non-empty list
            parallel_job_configs.append((idx, job_args_list))

    # Prepare common submit_and_wait arguments
    submit_kwargs = {
        "cluster_name": cluster_name,
        "project": config.project.get_config("ci_job.project"),
        "variables_overrides": overrides,
        "namespace": config.project.get_config("fournos.namespace"),
        "owner": config.project.get_config("fournos.job.owner"),
        "pipeline_name": config.project.get_config("fournos.job.pipeline_name"),
        "env": env_dict,
        "ci_label": config.project.get_config("fournos.job.ci_label"),
        "exclusive": config.project.get_config("fournos.job.exclusive"),
        "gpu_count": gpu_count,
        "gpu_type": gpu_type,
    }

    if parallel_job_configs:
        logger.info(
            f"Found {len(parallel_job_configs)} parallel job configurations, launching in parallel"
        )

        # Generate timestamp for parallel job names (shared across all parallel jobs)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        raw_name = f"forge-{project_name}-{timestamp}"
        raw_name = sanitize_k8s_name(raw_name)

        # Track failures and job names across parallel jobs
        failure_lock = threading.Lock()
        has_failures = [False]  # Use list for mutable reference
        submitted_job_names = []
        submitted_job_lock = threading.Lock()

        def submit_parallel_job(job_index, job_args_list):
            """Submit a single parallel job with specific args."""
            # Combine base ci_job.args with parallel job-specific args
            base_args = config.project.get_config("ci_job.args")
            combined_args = base_args + job_args_list

            logger.info(f"Submitting parallel job {job_index} with args: {combined_args}")

            # Create display name consistent with single job format
            args_str = " ".join(combined_args)
            parallel_display_name = f"{project_name} {args_str} (job {job_index})".strip()

            # Create unique job name with timestamp and index
            unique_job_name = sanitize_k8s_name(f"{raw_name}-{job_index}")

            try:
                # Track the job name for cleanup (job gets submitted even if waiting fails)
                with submitted_job_lock:
                    submitted_job_names.append(unique_job_name)

                # Create job-specific status directory
                job_status_dest = env.ARTIFACT_DIR / unique_job_name
                job_status_dest.mkdir(parents=True, exist_ok=True)

                submit_and_wait(
                    **submit_kwargs,
                    args=combined_args,
                    display_name=parallel_display_name,
                    job_name=unique_job_name,
                    artifact_dirname_suffix=str(job_index),
                    status_dest=job_status_dest,
                )
                logger.info(f"Parallel job {job_index} completed successfully")
            except Exception as e:
                logger.error(f"Parallel job {job_index} failed: {e}")
                traceback.print_exc()

                # Register failure in thread-safe way
                with failure_lock:
                    has_failures[0] = True

        # Submit all parallel jobs with exit_on_exception=False to let others complete
        with Parallel("parallel_jobs", exit_on_exception=False) as parallel:
            for job_index, job_args_list in parallel_job_configs:
                parallel.delayed(submit_parallel_job, job_index, job_args_list)

        # Cleanup all submitted jobs
        if submitted_job_names:
            logger.info(
                f"Cleaning up {len(submitted_job_names)} parallel jobs: {', '.join(submitted_job_names)}"
            )
            for job_name in submitted_job_names:
                try:
                    cleanup_fjob(
                        job_name=job_name,
                        namespace=config.project.get_config("fournos.namespace"),
                    )
                    logger.info(f"Cleaned up job: {job_name}")
                except Exception as cleanup_e:
                    logger.warning(f"Failed to cleanup job {job_name}: {cleanup_e}")

        # Check if any jobs failed
        if has_failures[0]:
            logger.error("One or more parallel jobs failed")
            return 1
        else:
            logger.info("All parallel jobs completed successfully")
            return 0
    else:
        # No parallel jobs configured, run single job as before
        logger.info("No parallel jobs configured, running single job")

        # Generate unique job name for single job
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        single_job_name = sanitize_k8s_name(f"forge-{project_name}-{timestamp}")

        try:
            submit_and_wait(
                **submit_kwargs,
                args=config.project.get_config("ci_job.args"),
                display_name=config.project.get_config("fournos.job.display_name"),
                job_name=single_job_name,
                status_dest=env.ARTIFACT_DIR,
            )
            logger.info("Single job completed successfully")
            return_code = 0
        except Exception as e:
            logger.error(f"Single job failed: {e}")
            return_code = 1

        # Cleanup the job
        try:
            cleanup_fjob(
                job_name=single_job_name,
                namespace=config.project.get_config("fournos.namespace"),
            )
            logger.info(f"Cleaned up job: {single_job_name}")
        except Exception as cleanup_e:
            logger.warning(f"Failed to cleanup job {single_job_name}: {cleanup_e}")

        return return_code
