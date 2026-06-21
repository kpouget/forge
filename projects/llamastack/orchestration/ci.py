#!/usr/bin/env python3
"""Llama Stack Testing CI Operations"""

from pathlib import Path

from projects.core.library.ci_base import create_ci_app

main = create_ci_app(
    project_name="llamastack",
    description="Llama Stack Testing CI Operations for FORGE.",
    config_dir=Path(__file__).parent,
    phases={
        "prepare": {
            "func": "projects.llamastack.orchestration.prepare_phase:run",
            "help": "Prepare phase - Deploy vLLM inference service.",
        },
        "test": {
            "func": "projects.llamastack.orchestration.test_phase:run",
            "help": "Test phase - Run load tests (auto-detects single or sweep from preset).",
        },
        "pre-cleanup": {
            "func": "projects.llamastack.orchestration.cleanup_phase:run",
            "help": "Pre-cleanup phase - Remove test resources (Locust jobs, LlamaStack, Postgres).",
        },
        "post-cleanup": {
            "func": "projects.llamastack.orchestration.cleanup_phase:run_platform_cleanup",
            "help": "Post-cleanup phase - Remove all resources and delete namespace.",
        },
    },
)

if __name__ == "__main__":
    main()
