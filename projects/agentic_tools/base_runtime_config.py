"""
Base runtime configuration for agentic testing projects.

Provides the shared foundation that every agentic project's runtime_config.py
needs: initialization, artifact directory setup, namespace derivation, and
common config accessors.  Each project subclasses this with project-specific
accessors and exposes a module-level ``cfg`` singleton.

Usage in a project's ``runtime_config.py``::

    from projects.agentic_tools.base_runtime_config import BaseRuntimeConfig

    class MyProjectConfig(BaseRuntimeConfig):
        def __init__(self) -> None:
            super().__init__(
                orchestration_dir=Path(__file__).resolve().parent,
                namespace_prefix="my-proj",
            )

        def get_some_project_setting(self) -> str:
            return config.project.get_config("runtime.some_setting")

    cfg = MyProjectConfig()

Consumers import the singleton directly::

    from projects.my_project.orchestration.runtime_config import cfg
    namespace = cfg.get_namespace()
"""

from __future__ import annotations

import copy
import logging
import re
from pathlib import Path
from typing import Any

from projects.core.library import config, env, run

logger = logging.getLogger(__name__)

_ARTIFACT_SUBDIRS = ("src", "artifacts", "artifacts/results")


class BaseRuntimeConfig:
    """Shared runtime configuration logic for agentic testing projects.

    Args:
        orchestration_dir: Absolute path to the project's ``orchestration/`` directory.
        namespace_prefix: Short prefix used when deriving a namespace from the job name
            (e.g. ``"mcp-gw"``, ``"ls-bench"``).
        max_namespace_length: Maximum k8s namespace length (default 63).
    """

    def __init__(
        self,
        orchestration_dir: Path,
        namespace_prefix: str,
        max_namespace_length: int = 63,
    ) -> None:
        self.orchestration_dir = orchestration_dir
        self.namespace_prefix = namespace_prefix
        self.max_namespace_length = max_namespace_length

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def init(self) -> Path:
        """Bootstrap logging, env, run, and config; create artifact dirs."""
        if not logging.getLogger().handlers:
            logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

        env.init()
        run.init()
        if config.project is None:
            config.init(self.orchestration_dir)
        self._ensure_artifact_directories(env.ARTIFACT_DIR)
        return env.ARTIFACT_DIR

    @staticmethod
    def _ensure_artifact_directories(artifact_dir: Path) -> None:
        for relative in _ARTIFACT_SUBDIRS:
            (artifact_dir / relative).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Common config accessors
    # ------------------------------------------------------------------

    def get_config_dir(self) -> Path:
        return self.orchestration_dir

    def get_namespace(self) -> str:
        override = config.project.get_config("runtime.namespace_override", None)
        if override:
            return override

        namespace = config.project.get_config("runtime.namespace")
        if namespace:
            return namespace

        job_name = self.get_job_name()
        return self.derive_namespace(
            job_name,
            prefix=self.namespace_prefix,
            max_length=self.max_namespace_length,
        )

    def get_job_name(self) -> str:
        job_name = config.project.get_config("runtime.job_name", None)
        if job_name:
            return job_name

        preset_name = self.get_preset_name()
        return f"local-{preset_name}"

    @staticmethod
    def get_preset_name() -> str:
        args = config.project.get_config("project.args", [])
        return "|".join(args) if args else "default"

    def get_manifests_dir(self) -> Path:
        return self.orchestration_dir / "manifests"

    @staticmethod
    def get_metrics_config() -> dict[str, Any]:
        """Return raw metrics capture configuration dict (validated by caller)."""
        return copy.deepcopy(config.project.get_config("metrics", {}))

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def derive_namespace(job_name: str, prefix: str, max_length: int) -> str:
        """Derive a valid k8s namespace from a job name and prefix."""
        slug = re.sub(r"[^a-z0-9-]+", "-", job_name.lower())
        slug = re.sub(r"-{2,}", "-", slug).strip("-")
        if not slug:
            slug = "run"

        if slug.startswith(f"{prefix}-"):
            namespace = slug
        else:
            namespace = f"{prefix}-{slug}"

        namespace = namespace[:max_length].rstrip("-")
        if not namespace:
            raise ValueError(f"Could not derive a valid namespace from job name: {job_name}")
        return namespace
