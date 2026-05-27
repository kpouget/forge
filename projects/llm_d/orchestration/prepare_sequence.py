from __future__ import annotations

from projects.llm_d.orchestration import prepare_phase


def run_prepare_sequence(
    *,
    artifact_dir,
    config_dir: str,
    namespace: str,
    namespace_is_managed: bool,
    platform: dict,
    model_key: str,
    model: dict,
    model_cache: dict,
    benchmark: dict | None,
) -> int:
    prepare_phase.verify_oc_access()
    prepare_phase.verify_cluster_version(platform=platform)

    prepare_phase.prepare_cert_manager(platform=platform)
    prepare_phase.prepare_leader_worker_set(platform=platform)
    prepare_phase.prepare_nfd(platform=platform)
    prepare_phase.prepare_gpu_operator(platform=platform)
    prepare_phase.prepare_rhoai_operator(platform=platform)
    prepare_phase.apply_datasciencecluster(config_dir=config_dir, rhoai=platform["rhoai"])
    prepare_phase.wait_for_datasciencecluster_ready(rhoai=platform["rhoai"])
    prepare_phase.ensure_required_crds(
        crd_names=platform["rhoai"]["required_crds_after_dsc"],
        rhoai=platform["rhoai"],
    )
    prepare_phase.ensure_gateway(config_dir=config_dir, gateway=platform["gateway"])
    prepare_phase.ensure_test_namespace(namespace=namespace)
    prepare_phase.cleanup_previous_run(
        namespace=namespace,
        inference_service_name=platform["inference_service"]["name"],
        cleanup_timeout_seconds=platform["cluster"]["cleanup_timeout_seconds"],
        benchmark_name=benchmark["job_name"] if benchmark else None,
    )
    prepare_phase.prepare_model_cache(
        namespace=namespace,
        namespace_is_managed=namespace_is_managed,
        model_key=model_key,
        model=model,
        model_cache=model_cache,
    )

    prepare_phase.verify_gpu_nodes(platform=platform)
    prepare_phase.capture_prepare_state(
        artifact_dir=artifact_dir,
        namespace=namespace,
        platform=platform,
    )
    return 0
