import logging
import signal

from projects.ag_praxis.toolbox.maas_sim_test import main as maas_sim_test_command
from projects.core.library import env
from projects.core.library.postprocess import (
    create_test_metadata,
    run_and_postprocess,
    update_test_labels_with_status,
    update_test_labels_with_timing,
)

logger = logging.getLogger(__name__)


def _signal_handler_sigint(sig, frame):
    """Sample SIGINT signal handler for AG Praxis project."""
    env.reset_artifact_dir()
    # Sample handler - does nothing


def _signal_handler_sigterm(sig, frame):
    """Sample SIGTERM signal handler for AG Praxis project."""
    env.reset_artifact_dir()
    # Sample handler - does nothing


def _setup_sample_signal_handlers():
    """Set up sample signal handlers for demonstration."""
    try:
        signal.signal(signal.SIGINT, _signal_handler_sigint)
        signal.signal(signal.SIGTERM, _signal_handler_sigterm)
        logger.debug("Sample signal handlers installed")
    except Exception as e:
        logger.warning(f"Failed to set up sample signal handlers: {e}")


def test():
    """Main test function that wraps do_test() with outcome postprocessing."""
    return run_and_postprocess(do_test)


def create_custom_test_metadata():
    labels = {
        "praxis": True,
    }
    test_dir = env.ARTIFACT_DIR
    create_test_metadata(
        test_dir,
        labels,
    )

    return test_dir


def do_test():
    logger.info("=== AG Praxis Project Test Phase ===")

    with env.NextArtifactDir("ag_praxis_test_dir"):
        test_dir = create_custom_test_metadata()
        try:
            update_test_labels_with_timing(test_dir, "test", "start")

            maas_sim_test_command.run(action="status")
            maas_sim_test_command.run(action="cleanup")
            maas_sim_test_command.run(action="deploy")
            maas_sim_test_command.run(action="status")
            maas_sim_test_command.run(action="test")
            maas_sim_test_command.run(action="cleanup")
            maas_sim_test_command.run(action="status")

        except Exception as e:
            logger.exception("❌ Test failed with exception")
            update_test_labels_with_status(test_dir, False, f"Test failed with exception: {str(e)}")

            raise
        finally:
            update_test_labels_with_timing(test_dir, "test", "end")

    update_test_labels_with_status(test_dir, True, "Test completed successfully")

    return 0


def fournos_resolve_hardware_request(hardware_spec: dict):
    """
    Resolve hardware requirements for FournosJob based on AG Praxis project configuration.

    This is a stub implementation. Update spec.hardware based on project configuration.

    Args:
        hardware_spec: The current spec.hardware dict from the FournosJob. This object should be updated.

    """
    logger.info("Hardware resolution: stub implementation - no changes made")

    # Stub implementation - could be extended to:
    # - Read hardware config from project configuration
    # - Set hardware requirements based on workload needs
    # - Handle different hardware profiles (GPU, CPU, memory requirements)
    # - Example: return {"gpu": {"type": "nvidia-tesla-v100", "count": 1}, "memory": "32Gi"}

    return hardware_spec
