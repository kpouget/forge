#!/usr/bin/env python3
"""MCP Gateway Project CI Operations"""

from pathlib import Path

from projects.core.library.ci_base import create_ci_app

main = create_ci_app(
    project_name="mcp_gateway",
    description="MCP Gateway Project CI Operations for FORGE.",
    config_dir=Path(__file__).parent,
    phases={
        "prepare": {
            "func": "projects.mcp_gateway.orchestration.prepare_phase:run",
            "help": "Prepare phase - Install platform at MCP_GATEWAY_VERSION.",
        },
        "test": {
            "func": "projects.mcp_gateway.orchestration.test_phase:run",
            "help": "Test phase - Execute load tests across the experiment matrix.",
        },
        "pre-cleanup": {
            "func": "projects.mcp_gateway.orchestration.cleanup_phase:run",
            "help": "Pre-cleanup phase - Remove test resources (Locust, mock server, routes).",
        },
        "post-cleanup": {
            "func": "projects.mcp_gateway.orchestration.cleanup_phase:run_platform_cleanup",
            "help": "Post-cleanup phase - Uninstall platform operators, CRs, and namespaces.",
        },
    },
    preset_optional_phases=["prepare", "post-cleanup"],
)

if __name__ == "__main__":
    main()
