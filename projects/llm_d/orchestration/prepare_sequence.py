from __future__ import annotations

from projects.llm_d.runtime import phase_inputs, runtime_config
from projects.llm_d.toolbox.cleanup.main import run as cleanup_toolbox_run
from projects.llm_d.toolbox.prepare import main as prepare_toolbox
from projects.llm_d.toolbox.prepare_model_cache.main import run as prepare_model_cache_toolbox_run


def run_prepare_sequence(config: runtime_config.ResolvedConfig) -> int:
    prepare_inputs = phase_inputs.build_prepare_inputs(
        artifact_dir=config.artifact_dir,
        config_dir=str(config.config_dir),
        preset_name=config.preset_name,
        namespace=config.namespace,
        namespace_is_managed=config.namespace_is_managed,
        platform=config.platform,
        model_key=config.model_key,
        model=config.model,
        model_cache=config.model_cache,
        benchmark=config.benchmark,
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

    cleanup_toolbox_run(
        namespace=prepare_inputs.namespace,
        inference_service_name=prepare_inputs.platform["inference_service"]["name"],
        cleanup_timeout_seconds=prepare_inputs.platform["cluster"]["cleanup_timeout_seconds"],
        benchmark_name=prepare_inputs.benchmark["job_name"] if prepare_inputs.benchmark else None,
    )
    prepare_model_cache_toolbox_run(
        namespace=prepare_inputs.namespace,
        namespace_is_managed=prepare_inputs.namespace_is_managed,
        model_key=prepare_inputs.model_key,
        model=prepare_inputs.model,
        model_cache=prepare_inputs.model_cache,
    )

    prepare_toolbox.verify_gpu_nodes(prepare_inputs)
    prepare_toolbox.capture_prepare_state(prepare_inputs)
    return 0
