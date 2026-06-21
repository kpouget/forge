from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

from projects.core.library import config
from projects.core.library.base_runtime_config import BaseRuntimeConfig

logger = logging.getLogger(__name__)

USERS_PER_WORKER = 16


class LlamaStackConfig(BaseRuntimeConfig):
    """Runtime configuration for Llama Stack performance benchmarks."""

    def __init__(self) -> None:
        super().__init__(
            orchestration_dir=Path(__file__).resolve().parent,
            namespace_prefix="ls-bench",
        )

    # --- Workload accessors ---

    def get_user_class(self) -> str:
        return config.project.get_config("runtime.user_class")

    def get_users(self) -> int:
        return int(config.project.get_config("runtime.users"))

    def get_spawn_rate(self) -> int:
        return int(config.project.get_config("runtime.spawn_rate"))

    def get_duration_seconds(self) -> int:
        return int(config.project.get_config("runtime.duration_seconds"))

    def get_warmup_seconds(self) -> int:
        return int(config.project.get_config("runtime.warmup_seconds"))

    def get_input_tokens(self) -> int:
        return int(config.project.get_config("runtime.input_tokens"))

    def get_output_tokens(self) -> int:
        return int(config.project.get_config("runtime.output_tokens"))

    def get_load_shape(self) -> str:
        return config.project.get_config("runtime.load_shape")

    # --- Model accessors ---

    def get_model_key(self) -> str:
        return config.project.get_config("runtime.model_key")

    def get_model_config(self) -> dict[str, Any]:
        key = self.get_model_key()
        return copy.deepcopy(config.project.get_config(f"models.{key}"))

    def get_model_name(self) -> str:
        return self.get_model_config()["name"]

    def get_model_id(self) -> str:
        return self.get_model_config()["model_id"]

    # --- LlamaStack accessors ---

    def get_distribution_name(self) -> str:
        return config.project.get_config("llamastack.distribution_name")

    def get_replicas(self) -> int:
        return int(config.project.get_config("llamastack.replicas"))

    def get_disable_otel(self) -> bool:
        return bool(config.project.get_config("llamastack.disable_otel", False))

    def get_enable_hpa(self) -> bool:
        return bool(config.project.get_config("llamastack.enable_hpa", False))

    def get_hpa_config(self) -> dict[str, Any]:
        return copy.deepcopy(config.project.get_config("llamastack.hpa", {}))

    def get_mcp_server_url(self) -> str | None:
        return config.project.get_config("llamastack.mcp_server_url", None)

    # --- Platform accessors ---

    def get_deploy_mcp_server(self) -> bool:
        return bool(config.project.get_config("llamastack.deploy_mcp_server", False))

    # --- Scheduling accessors ---

    def get_gpu_node_scheduling(self) -> dict[str, Any]:
        """Return scheduling config for GPU node (locust, llamastack pods)."""
        return copy.deepcopy(config.project.get_config("scheduling.gpu_node", {}))

    def get_worker_node_scheduling(self) -> dict[str, Any]:
        """Return scheduling config for non-GPU worker node (postgres)."""
        return copy.deepcopy(config.project.get_config("scheduling.worker_node", {}))

    # --- Experiment accessors ---

    def get_experiment_type(self) -> str:
        """Return experiment type: 'lls_overhead' or 'rhaiis_direct'."""
        return config.project.get_config("experiment.type", "lls_overhead")

    def get_replica_levels(self) -> list[int]:
        """Return list of replica counts to test. Falls back to single [replicas] value."""
        levels = config.project.get_config("experiment.replica_levels", None)
        if levels:
            return list(levels)
        return [self.get_replicas()]

    def get_concurrency_levels(self) -> list[int]:
        """Return list of concurrency levels to test. Falls back to single [users] value."""
        levels = config.project.get_config("experiment.concurrency_sweep.levels", None)
        if levels:
            return list(levels)
        return [self.get_users()]

    # --- Locust config builder ---

    def build_locust_config(self, *, namespace: str, users: int, job_name: str):
        """Assemble a complete LocustRunConfig from the current preset configuration."""
        import math

        from projects.agentic_tools.locust import locust_runtime, locust_users
        from projects.agentic_tools.locust.toolbox.run_distributed.main import LocustRunConfig

        model_config = self.get_model_config()
        experiment_type = self.get_experiment_type()
        distribution_name = self.get_distribution_name()
        gpu_scheduling = self.get_gpu_node_scheduling()

        workers = max(1, math.ceil(users / USERS_PER_WORKER))

        if experiment_type == "rhaiis_direct":
            host_url = f"http://{model_config['name']}-predictor.{namespace}.svc.cluster.local:8080"
        else:
            host_url = f"http://{distribution_name}-service.{namespace}.svc.cluster.local:8321"

        input_tokens = self.get_input_tokens()
        output_tokens = self.get_output_tokens()

        env_vars = {
            "USER_CLASS": self.get_user_class(),
            "MODEL": model_config["model_id"],
            "LOAD_SHAPE": self.get_load_shape(),
            "INPUT_TOKENS": str(input_tokens),
            "OUTPUT_TOKENS": str(output_tokens),
            "WARMUP_SECONDS": str(self.get_warmup_seconds()),
        }
        if input_tokens > 0:
            env_vars["LOCUST_OUTPUT_DIR"] = "/prompts"

        mcp_server_url = self.get_mcp_server_url()
        if mcp_server_url:
            env_vars["MCP_SERVER"] = mcp_server_url.format(namespace=namespace)

        users_dir = Path(locust_users.__file__).parent
        runtime_dir = Path(locust_runtime.__file__).parent

        return LocustRunConfig(
            job_name=job_name,
            namespace=namespace,
            host_url=host_url,
            users=users,
            workers=workers,
            duration_seconds=self.get_duration_seconds() + self.get_warmup_seconds(),
            spawn_rate=self.get_spawn_rate(),
            configmap_name="locust-scripts-llamastack",
            locustfiles_dir=runtime_dir,
            locustfile_names=["locustfile_main.py", "locust_shapes.py", "metrics_hook.py"],
            extra_files=[
                users_dir / "_common.py",
                users_dir / "responses_users.py",
                users_dir / "responses_simple_user.py",
                users_dir / "responses_mcp_user.py",
                users_dir / "responses_mcp_benchmark_user.py",
                users_dir / "chat_completions_user.py",
            ],
            env_vars=env_vars,
            labels={"forge.openshift.io/project": "llamastack"},
            extra_volumes=[
                {"name": "prompts", "configMap": {"name": "synthetic-prompts", "optional": True}},
            ],
            extra_volume_mounts=[
                {"name": "prompts", "mountPath": "/prompts"},
            ],
            node_selector=gpu_scheduling.get("node_selector"),
            tolerations=gpu_scheduling.get("tolerations"),
        )


cfg = LlamaStackConfig()
