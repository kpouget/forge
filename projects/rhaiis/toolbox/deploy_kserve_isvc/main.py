#!/usr/bin/env python3

from projects.core.dsl import (
    execute_tasks,
    shell,
    task,
    template,
    toolbox,
)


def run(
    *,
    deployment_name: str,
    namespace: str,
    model_id: str,
    vllm_image: str,
    accelerator: str = "nvidia",
    vllm_args: dict | None = None,
    env_vars: dict | None = None,
    replicas: int = 1,
    cpu_request: str = "4",
    memory_request: str = "16Gi",
    storage_source: str = "hf",
    storage_pvc: str = "",
    image_pull_secret: str = "",
    service_account_name: str = "",
):
    return execute_tasks(locals())


@task
def prepare_args(args, context):
    context.env_vars_list = []
    for key, value in (args.env_vars or {}).items():
        context.env_vars_list.append({"name": key, "value": str(value)})

    vllm_args = args.vllm_args or {}
    tp_size = vllm_args.get("tensor-parallel-size", 1)
    context.gpu_count = int(tp_size)

    if args.storage_source == "pvc" and args.storage_pvc:
        context.use_pvc = True
        context.pvc_name = args.storage_pvc
    elif args.storage_source == "hf" and args.storage_pvc:
        context.use_pvc = True
        context.pvc_name = args.storage_pvc
    else:
        context.use_pvc = False
        context.pvc_name = ""

    return f"GPU count={context.gpu_count}, env_vars={len(context.env_vars_list)}, pvc={context.use_pvc}"


@task
def ensure_namespace(args, context):
    result = shell.run(
        f"oc get namespace {args.namespace}",
        check=False,
    )
    if result.returncode != 0:
        shell.run(f"oc create namespace {args.namespace}")
        return f"Created namespace {args.namespace}"
    return f"Namespace {args.namespace} exists"


@task
def render_kserve_manifest(args, context):
    output_path = args.artifact_dir / "kserve.yaml"
    template.render_template_to_file("kserve.yaml.j2", output_path)
    context.kserve_manifest_path = output_path
    return f"Rendered KServe manifest to {output_path}"


@task
def apply_kserve_manifest(args, context):
    shell.run(f"oc apply -f {context.kserve_manifest_path}")
    return f"Applied ServingRuntime + InferenceService {args.deployment_name}"


main = toolbox.create_toolbox_main(run)

if __name__ == "__main__":
    main()
