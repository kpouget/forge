#!/usr/bin/env python3
"""
FORGE CLI Orchestration Entrypoint

This script provides the unified entrypoint for CLI operations across all projects
in the FORGE test harness. It kicks project cli.py files instead of individual
operation scripts, providing a consistent CLI interface.

Usage:
    run_cli                           # List available projects
    run_cli <project> <operation>     # Execute project CLI operation
    run_cli projects                  # Explicit project listing

Examples:
    run_cli llm_d prepare
    run_cli llm_d test
    run_cli skeleton validate
"""

from projects.core.ci_entrypoint.run_common import main_orchestrator

# Create main function for CLI mode
main = main_orchestrator(use_cli=True)

if __name__ == "__main__":
    main()
