from __future__ import annotations

import logging

from projects.core.dsl import always, execute_tasks, shell, task
from projects.llm_d.runtime import llmd_runtime
from projects.llm_d.toolbox.capture_llmisvc_state import main as capture_llmisvc_state
from projects.llm_d.toolbox.deploy_llmisvc import main as deploy_llmisvc
from projects.llm_d.toolbox.run_guidellm_benchmark import main as run_guidellm_benchmark_command
from projects.llm_d.toolbox.run_smoke_request import main as run_smoke_request_command

LOGGER = logging.getLogger(__name__)


def run(
    *,
    config_dir: str,
    namespace: str,
    inference_service: dict,
    gateway: dict,
    model_key: str,
    model: dict,
    scheduler_profile_key: str,
    scheduler_profile: dict | None,
    model_cache: dict,
    smoke: dict,
    smoke_request: dict,
    benchmark: dict | None = None,
    capture_namespace_events: bool = True,
) -> int:
    """Deploy llm_d, run the smoke request, and optionally execute GuideLLM.

    Args:
        config_dir: Configuration directory
        namespace: Namespace used by llm_d
        inference_service: Inference-service configuration block
        gateway: Gateway configuration block
        model_key: Selected model key
        model: Selected model configuration
        scheduler_profile_key: Scheduler profile key
        scheduler_profile: Scheduler profile configuration
        model_cache: Model-cache configuration
        smoke: Smoke configuration block
        smoke_request: Smoke-request configuration
        benchmark: Optional benchmark configuration
        capture_namespace_events: Whether namespace events should be captured
    """

    llmd_runtime.init()
    execute_tasks(locals())
    return 0


@task
def load_inputs(args, ctx):
    """Load the test phase inputs"""

    ctx.artifact_dir = args.artifact_dir
    ctx.namespace = args.namespace
    ctx.inference_service = args.inference_service
    ctx.smoke = args.smoke
    ctx.benchmark = args.benchmark
    ctx.capture_namespace_events = args.capture_namespace_events
    return f"Loaded test inputs for namespace {ctx.namespace}"


@task
def deploy_inference_service_task(args, ctx):
    """Deploy the LLMInferenceService and resolve its endpoint"""

    ctx.endpoint_url = deploy_llmisvc.run(
        config_dir=args.config_dir,
        namespace=args.namespace,
        inference_service=args.inference_service,
        gateway=args.gateway,
        model_key=args.model_key,
        model=args.model,
        scheduler_profile_key=args.scheduler_profile_key,
        scheduler_profile=args.scheduler_profile,
        model_cache=args.model_cache,
    )
    return f"Endpoint resolved: {ctx.endpoint_url}"


@task
def run_smoke_request_task(args, ctx):
    """Run the smoke request against the deployed service"""

    ctx.smoke_response = run_smoke_request_command.run(
        namespace=args.namespace,
        smoke=args.smoke,
        model=args.model,
        smoke_request=args.smoke_request,
        endpoint_url=ctx.endpoint_url,
    )
    return "Smoke request completed"


@task
def run_guidellm_benchmark_task(args, ctx):
    """Run the GuideLLM benchmark when enabled for the preset"""

    if not args.benchmark:
        return "GuideLLM benchmark disabled"

    run_guidellm_benchmark_command.run(
        namespace=args.namespace,
        benchmark=args.benchmark,
        endpoint_url=ctx.endpoint_url,
    )
    return f"GuideLLM benchmark {args.benchmark['job_name']} completed"


@always
@task
def capture_inference_service_state_task(args, ctx):
    """Capture the LLMInferenceService state and related resources"""

    namespace = getattr(ctx, "namespace", None)
    inference_service = getattr(ctx, "inference_service", None)
    if not namespace or not inference_service:
        return "Test inputs unavailable; skipping state capture"

    capture_llmisvc_state.run(
        llmisvc_name=inference_service["name"],
        namespace=namespace,
    )
    return "Inference-service artifacts captured"


@always
@task
def write_endpoint_url_task(args, ctx):
    """Persist the resolved endpoint URL when available"""

    artifact_dir = getattr(ctx, "artifact_dir", None)
    if not artifact_dir:
        return "Test inputs unavailable; skipping endpoint capture"

    endpoint_url = getattr(ctx, "endpoint_url", None)
    if not endpoint_url:
        return "Endpoint URL not available"

    llmd_runtime.write_text(artifact_dir / "artifacts" / "endpoint.url", f"{endpoint_url}\n")
    return "Endpoint URL captured"


@always
@task
def cleanup_runtime_resources_task(args, ctx):
    """Delete smoke and benchmark helper resources"""

    namespace = getattr(ctx, "namespace", None)
    inference_service = getattr(ctx, "inference_service", None)
    smoke = getattr(ctx, "smoke", None)
    benchmark = getattr(ctx, "benchmark", None)
    if not namespace or not inference_service or not smoke:
        return "Test inputs unavailable; skipping cleanup"

    benchmark_name = benchmark["job_name"] if benchmark else "guidellm-benchmark"
    smoke_job_name = smoke["job_name"]

    llmd_runtime.oc(
        "delete",
        "job",
        smoke_job_name,
        "-n",
        namespace,
        "--ignore-not-found=true",
        check=False,
    )
    llmd_runtime.oc(
        "delete",
        "job,pvc",
        benchmark_name,
        "-n",
        namespace,
        "--ignore-not-found=true",
        check=False,
    )
    llmd_runtime.oc(
        "delete",
        "pod",
        f"{benchmark_name}-copy",
        "-n",
        namespace,
        "--ignore-not-found=true",
        check=False,
    )
    return "Test helper resources deleted"


@always
@task
def capture_namespace_events_task(args, ctx):
    """Capture namespace events after the test run"""

    artifact_dir = getattr(ctx, "artifact_dir", None)
    namespace = getattr(ctx, "namespace", None)
    capture_namespace_events = getattr(ctx, "capture_namespace_events", False)
    if not artifact_dir or not namespace or not capture_namespace_events:
        return "Test inputs unavailable; skipping namespace events capture"

    shell.run(
        f"oc get events -n {namespace} --sort-by=.metadata.creationTimestamp",
        check=False,
        stdout_dest=artifact_dir / "artifacts" / "namespace.events.txt",
    )
    return "Namespace events captured"
