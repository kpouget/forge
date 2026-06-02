import logging

from projects.llm_d.orchestration import prepare_phase

logger = logging.getLogger(__name__)


def run_prepare_sequence() -> int:
    """Run the prepare phase sequence using global config"""
    prepare_phase.verify_oc_access()
    prepare_phase.verify_cluster_version()
    prepare_phase.prepare_cert_manager()
    prepare_phase.prepare_leader_worker_set()
    prepare_phase.prepare_nfd()
    prepare_phase.prepare_gpu_operator()
    prepare_phase.prepare_rhoai_operator()
    prepare_phase.apply_datasciencecluster()
    prepare_phase.wait_for_datasciencecluster_ready()
    prepare_phase.ensure_required_crds()
    prepare_phase.ensure_gateway()
    prepare_phase.ensure_test_namespace()
    prepare_phase.cleanup_previous_run()
    prepare_phase.prepare_model_cache()
    prepare_phase.verify_gpu_nodes()
    prepare_phase.capture_prepare_state()
    logger.info("Prepare sequence completed successfully - all phases executed without errors")
    return 0
