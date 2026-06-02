#!/usr/bin/env python3

from __future__ import annotations

from projects.core.dsl import entrypoint, execute_tasks, retry, task


@entrypoint
def run(
    *,
    datasciencecluster_name: str,
    namespace: str,
) -> int:
    """
    Wait for the llm_d DataScienceCluster to become ready.

    Args:
        datasciencecluster_name: Name of the DataScienceCluster to wait for
        namespace: Namespace containing the DataScienceCluster
    """

    execute_tasks(locals())
    return 0


@retry(attempts=90, delay=10, backoff=1.0)
@task
def wait_for_datasciencecluster_ready(args, ctx):
    """Wait for the DataScienceCluster phase to become Ready"""

    from projects.core.dsl.utils.k8s import oc

    # Query only the status.phase field and show output
    result = oc(
        "get",
        "datasciencecluster",
        args.datasciencecluster_name,
        "-n",
        args.namespace,
        "-o",
        "jsonpath={.status.phase}",
        capture_output=True,
        log_stdout=True,  # Show the output
    )
    phase = result.stdout.strip() if result.stdout else None

    if phase == "Ready":
        return "DataScienceCluster ready"
    if phase in {"Failed", "Error"}:
        raise RuntimeError(f"DataScienceCluster entered terminal phase {phase}")
    return False  # Retry


if __name__ == "__main__":
    run.main()
