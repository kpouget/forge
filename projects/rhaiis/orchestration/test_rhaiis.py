import logging
import pathlib

from projects.core.library import config, env, run

logger = logging.getLogger(__name__)


def init():
    env.init()
    run.init()
    config.init(pathlib.Path(__file__).parent)


def _merge_vllm_args(
    defaults: dict,
    model: dict,
    workload: dict,
) -> dict:
    merged = dict(defaults)
    merged.update(model.get("vllm_args", {}))
    merged.update(workload.get("vllm_args", {}))
    return merged


def _merge_env_vars(accelerator: str, model: dict) -> dict:
    base = dict(config.project.get_config("rhaiis.env_vars") or {})
    base.update(model.get("env_vars", {}))
    accel_vars = config.project.get_config(f"rhaiis.accelerator_env_vars.{accelerator}") or {}
    base.update(accel_vars)
    return base


def _build_guidellm_args(
    *, model_id: str, data: str, rates_str: str, max_seconds: int, backend_type: str, rate_type: str
) -> list[str]:
    return [
        f"--model={model_id}",
        f"--data={data}",
        f"--rate={rates_str}",
        f"--max-seconds={max_seconds}",
        f"--backend={backend_type}",
        f"--profile={rate_type}",
        "--output-dir=/results",
        "--outputs=json",
    ]


def _split_image_tag(full_image: str) -> tuple[str, str]:
    if ":" in full_image:
        parts = full_image.rsplit(":", 1)
        return parts[0], parts[1]
    return full_image, "latest"


@config.requires(
    model_key="tests.rhaiis.model_key",
    workload_key="tests.rhaiis.workload_key",
    namespace="rhaiis.namespace",
)
def test(_cfg):
    model = config.project.get_config(f"models.{_cfg.model_key}")
    workload = config.project.get_config(f"workloads.{_cfg.workload_key}")
    accelerator = config.project.get_config("rhaiis.accelerator")
    deploy_cfg = config.project.get_config("rhaiis.deploy")
    benchmark_cfg = config.project.get_config("benchmarks.guidellm")

    hf_id = model["hf_model_id"]
    parts = hf_id.split("/")
    deployment_name = (parts[1] if len(parts) > 1 else hf_id).lower().replace(".", "-")

    image = config.project.get_config(f"rhaiis.images.{accelerator}")
    vllm_defaults = config.project.get_config("rhaiis.vllm_args")
    vllm_args = _merge_vllm_args(vllm_defaults, model, workload)
    env_vars = _merge_env_vars(accelerator, model)

    logger.info(
        f"Testing model={model['name']} workload={_cfg.workload_key} accelerator={accelerator}"
    )

    from projects.guidellm.toolbox.run_guidellm_benchmark.main import (
        run as run_guidellm_benchmark,
    )
    from projects.guidellm.toolbox.run_guidellm_benchmark.main import (
        wait_guidellm_benchmark_task,
    )
    from projects.rhaiis.toolbox.deploy_kserve_isvc.main import (
        run as deploy_kserve_isvc,
    )
    from projects.rhaiis.toolbox.wait_isvc_ready.main import (
        run as wait_isvc_ready,
    )

    benchmark_timeout = benchmark_cfg.get("timeout", 14400)
    wait_guidellm_benchmark_task._retry_config["attempts"] = max(1, benchmark_timeout // 10)

    try:
        deploy_kserve_isvc(
            deployment_name=deployment_name,
            namespace=_cfg.namespace,
            model_id=model["hf_model_id"],
            vllm_image=image,
            accelerator=accelerator,
            vllm_args=vllm_args,
            env_vars=env_vars,
            replicas=deploy_cfg.get("replicas", 1),
            cpu_request=deploy_cfg.get("cpu_request", "4"),
            memory_request=deploy_cfg.get("memory_request", "16Gi"),
            storage_source=deploy_cfg.get("storage_source", "hf"),
            storage_pvc=deploy_cfg.get("storage_pvc", ""),
            image_pull_secret=deploy_cfg.get("image_pull_secret", ""),
            service_account_name=deploy_cfg.get("service_account_name", ""),
        )

        wait_isvc_ready(
            name=deployment_name,
            namespace=_cfg.namespace,
            timeout_seconds=deploy_cfg.get("ready_timeout", 3600),
            health_check_timeout=deploy_cfg.get("health_check_timeout", 120),
        )

        endpoint_url = f"http://{deployment_name}-predictor.{_cfg.namespace}.svc.cluster.local:8080"

        rates_str = ",".join(str(r) for r in workload.get("rates", [1]))

        benchmark_image = benchmark_cfg.get("image", "ghcr.io/vllm-project/guidellm:v0.6.0")
        image, version = _split_image_tag(benchmark_image)

        guidellm_args = _build_guidellm_args(
            model_id=model["hf_model_id"],
            data=workload["data"],
            rates_str=rates_str,
            max_seconds=workload.get("max_seconds", 180),
            backend_type=benchmark_cfg.get("backend_type", "openai_http"),
            rate_type=benchmark_cfg.get("rate_type", "concurrent"),
        )

        run_guidellm_benchmark(
            endpoint_url=f"{endpoint_url}/v1",
            name=f"guidellm-{deployment_name}",
            namespace=_cfg.namespace,
            image=image,
            version=version,
            timeout=benchmark_timeout,
            pvc_size=benchmark_cfg.get("pvc_size", "5Gi"),
            guidellm_args=guidellm_args,
        )
    finally:
        from projects.rhaiis.toolbox.capture_isvc_state.main import (
            run as capture_isvc_state,
        )

        try:
            capture_isvc_state(
                name=deployment_name,
                namespace=_cfg.namespace,
            )
        except Exception:
            logger.warning("Capture failed, continuing with cleanup")

        from projects.rhaiis.toolbox.cleanup_isvc.main import (
            run as cleanup_isvc,
        )

        try:
            cleanup_isvc(
                name=deployment_name,
                namespace=_cfg.namespace,
            )
        except Exception:
            logger.warning("Cleanup failed")
