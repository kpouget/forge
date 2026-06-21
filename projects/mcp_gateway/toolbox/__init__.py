"""
MCP Gateway Performance Testing Toolbox

Project-specific commands for MCP Gateway platform management and test infrastructure.
Shared tools (Locust runner, MCP mock servers) are in projects.agentic_tools.

Modules:
    platform_helpers         - Shared utilities (step lookup, namespace wait, best-effort oc)
    install_platform/        - Full MCP Gateway platform stack install
    cleanup_platform/        - Reverse of install_platform
    apply_infrastructure/    - Generate HTTPRoute + MCPServerRegistration CRDs
    cleanup_test_resources/  - Remove Locust, mock server, infrastructure resources
"""
