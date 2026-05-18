from __future__ import annotations

from projects.llm_d.runtime import phase_inputs, runtime_config
from projects.llm_d.toolbox.cleanup.main import run as cleanup_toolbox_run
from projects.llm_d.toolbox.prepare import main as prepare_toolbox
from projects.llm_d.toolbox.prepare_model_cache.main import run as prepare_model_cache_toolbox_run


def run_prepare_sequence(config: runtime_config.ResolvedConfig) -> int:
    prepare_inputs = phase_inputs.build_prepare_inputs(
        artifact_dir=config.artifact_dir,
        **phase_inputs.prepare_kwargs(config),
    )

    prepare_toolbox.verify_oc_access()
    prepare_toolbox.verify_cluster_version(prepare_inputs)

    prepare_toolbox.prepare_cert_manager(prepare_inputs)
    prepare_toolbox.prepare_leader_worker_set(prepare_inputs)
    prepare_toolbox.prepare_nfd(prepare_inputs)
    prepare_toolbox.prepare_gpu_operator(prepare_inputs)
    prepare_toolbox.prepare_rhoai_operator(prepare_inputs)
    prepare_toolbox.apply_datasciencecluster(prepare_inputs)
    prepare_toolbox.wait_for_datasciencecluster_ready(prepare_inputs)
    prepare_toolbox.ensure_required_crds(
        prepare_inputs.platform["rhoai"]["required_crds_after_dsc"],
        prepare_inputs,
    )
    prepare_toolbox.ensure_gateway(prepare_inputs)
    prepare_toolbox.ensure_test_namespace(prepare_inputs)

    cleanup_toolbox_run(**phase_inputs.cleanup_kwargs(prepare_inputs))
    prepare_model_cache_toolbox_run(**phase_inputs.prepare_model_cache_kwargs(prepare_inputs))

    prepare_toolbox.verify_gpu_nodes(prepare_inputs)
    prepare_toolbox.capture_prepare_state(prepare_inputs)
    return 0
