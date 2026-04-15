#!/usr/bin/env python3

from __future__ import annotations

from projects.core.dsl import toolbox
from projects.llm_d.orchestration import llmd_runtime


def run() -> int:
    llmd_runtime.init()
    config = llmd_runtime.load_run_configuration()
    return run_cleanup(config)


def run_cleanup(config: llmd_runtime.ResolvedConfig) -> int:
    inference_service_name = config.platform["inference_service"]["name"]
    benchmark_name = (
        config.benchmark["job_name"] if config.benchmark else "guidellm-benchmark"
    )

    if config.namespace_is_managed:
        if llmd_runtime.resource_exists("namespace", config.namespace):
            llmd_runtime.oc(
                "delete", "namespace", config.namespace, "--ignore-not-found=true"
            )
            llmd_runtime.wait_for_namespace_deleted(
                config.namespace,
                timeout_seconds=config.platform["cluster"]["cleanup_timeout_seconds"],
            )
    else:
        llmd_runtime.oc(
            "delete",
            "llminferenceservice",
            inference_service_name,
            "-n",
            config.namespace,
            "--ignore-not-found=true",
            check=False,
        )
        llmd_runtime.oc(
            "delete",
            "job,pvc",
            benchmark_name,
            "-n",
            config.namespace,
            "--ignore-not-found=true",
            check=False,
        )
        llmd_runtime.oc(
            "delete",
            "pod",
            f"{benchmark_name}-copy",
            "-n",
            config.namespace,
            "--ignore-not-found=true",
            check=False,
        )

    return 0


main = toolbox.create_toolbox_main(run)


if __name__ == "__main__":
    main()
