#!/usr/bin/env python3
"""
FORGE CI Orchestration Entrypoint

This script provides the unified entrypoint for CI operations across all projects
in the FORGE test harness. It follows the constitutional principle of
CI-First Testing by providing consistent, reliable CI integration.

Usage:
    run                           # List available projects
    run <project> <operation>     # Execute project operation
    run projects                  # Explicit project listing

Examples:
    run llm_d prepare
    run llm_d test
    run skeleton validate
"""

from projects.core.ci_entrypoint.run_common import main_orchestrator

# Create main function for CI mode
main = main_orchestrator(use_cli=False)

if __name__ == "__main__":
    main()
