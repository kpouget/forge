#!/usr/bin/env python3
"""RHAIIS CLI - Interactive CLI for KServe InferenceService benchmarking.

Examples:
    # Quick test with defaults (qwen3-0.6b, balanced workload)
    python -m projects.rhaiis.orchestration.cli test \
        --namespace kserve-e2e-perf

    # Specific model and workload
    python -m projects.rhaiis.orchestration.cli test \
        --model llama-3-1-8b-fp8 \
        --workload short \
        --namespace kserve-e2e-perf \
        --image-pull-secret npalaska-image-pull

    # Dry run
    python -m projects.rhaiis.orchestration.cli test \
        --model qwen3-0_6b --dry-run

    # Cleanup only
    python -m projects.rhaiis.orchestration.cli cleanup \
        --deployment-name rhaiis-bench \
        --namespace kserve-e2e-perf
"""

import logging
import pathlib
import types

import click

from projects.core.library import config, env, run

logger = logging.getLogger(__name__)


def _init():
    env.init()
    run.init()
    config.init(pathlib.Path(__file__).parent)


def _merge_vllm_args(defaults: dict, model: dict, workload: dict) -> dict:
    merged = dict(defaults)
    merged.update(model.get("vllm_args", {}))
    merged.update(workload.get("vllm_args", {}))
    return merged


def _merge_env_vars(accelerator: str, model: dict) -> dict:
    base = dict(config.project.get_config("rhaiis.env_vars") or {})
    base.update(model.get("env_vars", {}))
    accel_vars = config.project.get_config(
        f"rhaiis.accelerator_env_vars.{accelerator}"
    ) or {}
    base.update(accel_vars)
    return base


@click.group()
@click.pass_context
def cli(ctx):
    """RHAIIS CLI - KServe InferenceService benchmarking."""
    ctx.ensure_object(types.SimpleNamespace)
    _init()


@cli.command()
@click.option("--model", "-m", default="qwen3-0_6b", help="Model key from config.yaml")
@click.option("--workload", "-w", default="balanced", help="Workload profile name")
@click.option("--namespace", "-n", default=None, help="Kubernetes namespace")
@click.option("--deployment-name", default=None, help="Deployment name (defaults to model name)")
@click.option("--accelerator", type=click.Choice(["nvidia", "amd"]), default=None)
@click.option("--vllm-image", help="vLLM container image override")
@click.option("--tensor-parallel", "-tp", type=int, help="Tensor parallel size override")
@click.option("--replicas", "-r", type=int, default=None)
@click.option("--storage-source", type=click.Choice(["hf", "pvc"]), default=None)
@click.option("--storage-pvc", help="PVC name for model storage")
@click.option("--image-pull-secret", help="Image pull secret name")
@click.option("--service-account-name", help="Service account name for predictor")
@click.option("--max-seconds", type=int, help="Max benchmark duration per rate")
@click.option("--rates", help="Comma-separated rates (e.g. 1,50,100)")
@click.option("--dry-run", is_flag=True, help="Print what would be done")
@click.pass_context
def test(
    ctx,
    model: str,
    workload: str,
    namespace: str | None,
    deployment_name: str | None,
    accelerator: str | None,
    vllm_image: str | None,
    tensor_parallel: int | None,
    replicas: int | None,
    storage_source: str | None,
    storage_pvc: str | None,
    image_pull_secret: str | None,
    service_account_name: str | None,
    max_seconds: int | None,
    rates: str | None,
    dry_run: bool,
):
    """Run KServe InferenceService benchmark."""
    model_cfg = config.project.get_config(f"models.{model}")
    workload_cfg = config.project.get_config(f"workloads.{workload}")
    deploy_cfg = config.project.get_config("rhaiis.deploy")
    benchmark_cfg = config.project.get_config("benchmarks.guidellm")

    if not deployment_name:
        hf_id = model_cfg["hf_model_id"]
        parts = hf_id.split("/")
        deployment_name = (parts[1] if len(parts) > 1 else hf_id).lower().replace(".", "-")

    accelerator = accelerator or config.project.get_config("rhaiis.accelerator")
    namespace = namespace or config.project.get_config("rhaiis.namespace")
    vllm_image = vllm_image or config.project.get_config(f"rhaiis.images.{accelerator}")
    replicas = replicas if replicas is not None else deploy_cfg.get("replicas", 1)
    storage_source = storage_source or deploy_cfg.get("storage_source", "hf")
    storage_pvc = storage_pvc or deploy_cfg.get("storage_pvc", "")
    image_pull_secret = image_pull_secret or deploy_cfg.get("image_pull_secret", "")
    service_account_name = service_account_name or deploy_cfg.get("service_account_name", "")

    vllm_defaults = config.project.get_config("rhaiis.vllm_args")
    vllm_args = _merge_vllm_args(vllm_defaults, model_cfg, workload_cfg)
    env_vars = _merge_env_vars(accelerator, model_cfg)

    if tensor_parallel is not None:
        vllm_args["tensor-parallel-size"] = tensor_parallel

    rate_list = [int(r) for r in rates.split(",")] if rates else workload_cfg.get("rates", [1])
    max_seconds_val = max_seconds or workload_cfg.get("max_seconds", 180)

    if dry_run:
        click.echo("[DRY-RUN] RHAIIS Benchmark Test")
        click.echo(f"  Model: {model} ({model_cfg['hf_model_id']})")
        click.echo(f"  Workload: {workload}")
        click.echo(f"  Namespace: {namespace}")
        click.echo(f"  Deployment: {deployment_name}")
        click.echo(f"  Accelerator: {accelerator}")
        click.echo(f"  Image: {vllm_image}")
        click.echo(f"  vLLM args: {vllm_args}")
        click.echo(f"  Env vars: {env_vars}")
        click.echo(f"  Replicas: {replicas}")
        click.echo(f"  Storage: {storage_source} (pvc={storage_pvc})")
        click.echo(f"  Image pull secret: {image_pull_secret or '(none)'}")
        click.echo(f"  Service account: {service_account_name or '(none)'}")
        click.echo(f"  Rates: {rate_list}")
        click.echo(f"  Max seconds: {max_seconds_val}")
        return

    from projects.rhaiis.toolbox.deploy_kserve_isvc.main import run as deploy_kserve_isvc

    click.echo(f"Deploying {model_cfg['hf_model_id']} to {namespace}/{deployment_name}")
    deploy_kserve_isvc(
        deployment_name=deployment_name,
        namespace=namespace,
        model_id=model_cfg["hf_model_id"],
        vllm_image=vllm_image,
        accelerator=accelerator,
        vllm_args=vllm_args,
        env_vars=env_vars,
        replicas=replicas,
        cpu_request=deploy_cfg.get("cpu_request", "4"),
        memory_request=deploy_cfg.get("memory_request", "16Gi"),
        storage_source=storage_source,
        storage_pvc=storage_pvc,
        image_pull_secret=image_pull_secret,
        service_account_name=service_account_name,
    )

    from projects.rhaiis.toolbox.wait_isvc_ready.main import run as wait_isvc_ready

    click.echo("Waiting for InferenceService to be ready...")
    wait_isvc_ready(
        name=deployment_name,
        namespace=namespace,
        timeout_seconds=deploy_cfg.get("ready_timeout", 3600),
        health_check_timeout=deploy_cfg.get("health_check_timeout", 120),
    )

    from projects.rhaiis.toolbox.run_guidellm_benchmark.main import run as run_guidellm_benchmark

    endpoint_url = (
        f"http://{deployment_name}-predictor"
        f".{namespace}.svc.cluster.local:8080"
    )

    rates_str = ",".join(str(r) for r in rate_list)
    click.echo(f"Running benchmark at rates={rates_str}...")

    benchmark_error = None
    try:
        run_guidellm_benchmark(
            namespace=namespace,
            deployment_name=deployment_name,
            endpoint_url=endpoint_url,
            model_id=model_cfg["hf_model_id"],
            data=workload_cfg["data"],
            rates=rates_str,
            max_seconds=max_seconds_val,
            benchmark_image=benchmark_cfg.get("image", "ghcr.io/vllm-project/guidellm:v0.6.0"),
            backend_type=benchmark_cfg.get("backend_type", "openai_http"),
            rate_type=benchmark_cfg.get("rate_type", "concurrent"),
            timeout=benchmark_cfg.get("timeout", 900),
            pvc_size=benchmark_cfg.get("pvc_size", "5Gi"),
        )
    except Exception as exc:
        benchmark_error = exc
        click.echo(f"Benchmark failed: {exc}")
    finally:
        from projects.rhaiis.toolbox.capture_isvc_state.main import run as capture_isvc_state

        click.echo("Capturing state...")
        try:
            capture_isvc_state(name=deployment_name, namespace=namespace)
        except Exception as exc:
            click.echo(f"Warning: capture failed: {exc}")

        from projects.rhaiis.toolbox.cleanup_isvc.main import run as cleanup_isvc

        click.echo("Cleaning up...")
        try:
            cleanup_isvc(name=deployment_name, namespace=namespace)
        except Exception as exc:
            click.echo(f"Warning: cleanup failed: {exc}")

    if benchmark_error:
        raise SystemExit(1)

    click.echo("Benchmark completed successfully.")


@cli.command()
@click.option("--deployment-name", required=True, help="InferenceService name")
@click.option("--namespace", "-n", default="forge-rhaiis", help="Kubernetes namespace")
@click.pass_context
def cleanup(ctx, deployment_name: str, namespace: str):
    """Cleanup InferenceService deployment."""
    from projects.rhaiis.toolbox.capture_isvc_state.main import run as capture_isvc_state
    from projects.rhaiis.toolbox.cleanup_isvc.main import run as cleanup_isvc

    click.echo(f"Capturing state for {deployment_name}...")
    capture_isvc_state(name=deployment_name, namespace=namespace)

    click.echo(f"Cleaning up {deployment_name}...")
    cleanup_isvc(name=deployment_name, namespace=namespace)

    click.echo("Cleanup completed.")


if __name__ == "__main__":
    cli()
