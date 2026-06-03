#!/usr/bin/env python3

"""
On Failure Agent - An agent that handles failure scenarios using LangChain

This agent analyzes FORGE test failures by processing FAILURE files and execution logs.
It supports both DSL toolbox logs (task.log) and legacy execution logs (_ansible.log).

This agent provides:
- LangChain-based model interaction
- Programmatic interface for other Python modules
- Click CLI for manual triggering
- Artifact directory processing
- Structured analysis extraction
- Task failure pattern recognition ("==> TASK FAILED:" in DSL logs)

Example usage:
    from projects.core.agentic.on_failure import run_on_failure_agent, get_failure_explanations

    # Full analysis with metadata
    result = run_on_failure_agent(Path("/path/to/artifacts"))
    for analysis in result['analyses']:
        structured = analysis['structured_analysis']
        print(f"Root Cause: {structured['root_cause']}")
        print(f"Failed Step: {structured['failed_step']}")

    # Simplified - just structured explanations
    explanations = get_failure_explanations(Path("/path/to/artifacts"))
    for exp in explanations:
        print(f"Root Cause: {exp['root_cause']}")
        print(f"Failed Step: {exp['failed_step']}")
"""

import functools
import json
import logging
import os
import re
import sys
import warnings
from datetime import datetime
from pathlib import Path

import click
import httpx
import urllib3
import yaml
from langchain_core.messages import HumanMessage

from projects.core.agentic.analysis_utils import extract_structured_analysis
from projects.core.agentic.artifact_processing import (
    find_execution_logs,
    find_failure_files,
    list_all_files_in_artifact_dir,
    read_execution_log_errors,
    read_failure_and_ansible_log,
    read_failure_and_log,
    read_failures_file,
    read_run_log,
)
from projects.core.agentic.models import create_llm_client, load_model_config
from projects.core.agentic.on_failure.cli import cli
from projects.core.agentic.on_failure.report import generate_html_report, text_to_code_block

from .queries import (
    FailureAnalysisQueries,
    execute_query_sequence,
)

MODEL_KEY = "qwen-3-6-35b"

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# Suppress HTTP request logging
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("langchain_openai").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def agent_review_on_failure(func):
    """
    Decorator to automatically trigger failure agent when CI command fails.

    Usage:
        @main.command()
        @click.pass_context
        @ci_lib.safe_ci_command
        @agent_review_on_failure
        def test(ctx) -> int:
            return test_toolbox_run()
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        function_name = func.__name__
        logger.info(f"🤖 Agent review enabled for CI command: {function_name}")

        try:
            exit_code = func(*args, **kwargs)
        except Exception as e:
            logger.error(f"🤖 Exception in CI command '{function_name}': {e}")
            _try_run_agent_on_exception()
            raise e

        # Success - no agent needed
        if exit_code == 0:
            logger.info(f"✅ CI command '{function_name}' succeeded - no agent review needed")
            return exit_code

        # Failure - run agent
        logger.warning(
            f"❌ CI command '{function_name}' failed with exit code {exit_code} - triggering failure agent"
        )
        _run_agent_and_log_results()
        return exit_code

    return wrapper


def _get_artifact_dir_with_failures():
    """Get and validate artifact directory, checking for FAILURE files"""
    artifact_dir_env = os.environ.get("ARTIFACT_DIR")
    if not artifact_dir_env:
        logger.warning("🤖 ARTIFACT_DIR not set - cannot run failure agent")
        return None

    artifact_dir = Path(artifact_dir_env)
    if not artifact_dir.exists():
        logger.warning(
            f"🤖 Artifact directory not found: {artifact_dir} - cannot run failure agent"
        )
        return None

    # Check for FAILURE files
    failure_files = list(artifact_dir.glob("**/FAILURE"))
    if not failure_files:
        logger.info(f"🤖 No FAILURE files found in {artifact_dir} - agent review not needed")
        return None

    logger.info(f"🤖 Found {len(failure_files)} FAILURE file(s) - agent review will proceed")
    return artifact_dir


def _run_agent_and_log_results():
    """Run failure agent and log results"""
    artifact_dir = _get_artifact_dir_with_failures()
    if not artifact_dir:
        return

    logger.info(f"🤖 Running failure agent on: {artifact_dir}")
    try:
        agent_result = run_on_failure_agent(artifact_dir, verbose=False)
        _log_agent_results(agent_result)
    except Exception as agent_error:
        logger.error(f"🤖 Exception while running failure agent: {agent_error}")


def _log_agent_results(agent_result):
    """Log agent analysis results"""
    if agent_result.get("status") != "success":
        logger.error(f"🤖 Agent analysis failed: {agent_result.get('error', 'unknown error')}")
        return

    failures_found = agent_result.get("failures_found", 0)
    successful_analyses = agent_result.get("successful_analyses", 0)
    logger.info(
        f"🤖 Agent analysis complete: {failures_found} failures found, {successful_analyses} successfully analyzed"
    )

    # Log key findings
    analyses = agent_result.get("analyses", [])
    for i, analysis in enumerate(analyses, 1):
        if analysis.get("status") == "success":
            _log_single_analysis(analysis, i)
        else:
            logger.warning(
                f"🤖 Failure #{i}: Agent analysis failed - {analysis.get('error', 'unknown error')}"
            )


def _log_single_analysis(analysis, index):
    """Log a single analysis result"""
    structured = analysis.get("structured_analysis", {})
    failure_dir = analysis.get("failure_dir", "unknown")

    logger.info(f"🤖 Failure #{index} ({failure_dir}):")
    if structured.get("root_cause"):
        logger.info(f"   🔍 Root Cause: {structured['root_cause']}")
    if structured.get("failed_step"):
        logger.info(f"   ❌ Failed Step: {structured['failed_step']}")


def _try_run_agent_on_exception():
    """Try to run agent when CI command throws exception"""
    artifact_dir = _get_artifact_dir_with_failures()
    if not artifact_dir:
        return

    try:
        logger.info("🤖 Running failure agent after exception")
        run_on_failure_agent(artifact_dir, verbose=False)
    except Exception as agent_error:
        logger.error(f"🤖 Exception while running failure agent: {agent_error}")


def _generate_unique_failure_review_path(base_artifact_dir: Path, failure_dir_name: str) -> Path:
    """
    Generate a unique path for FAILURE_REVIEW file in notifications directory, adding suffix if file already exists

    Args:
        base_artifact_dir: Base artifact directory path
        failure_dir_name: Name of the failure directory

    Returns:
        Unique path for the FAILURE_REVIEW file in 000__ci_metadata/notifications/
    """
    # Create notifications directory
    notifications_dir = base_artifact_dir / "000__ci_metadata" / "notifications"
    notifications_dir.mkdir(parents=True, exist_ok=True)

    base_filename = f"090__FAILURE_REVIEW_{failure_dir_name}.txt"

    # Check for existing files and add suffix if needed
    failure_review_path = notifications_dir / base_filename
    counter = 1
    while failure_review_path.exists():
        counter += 1
        filename_parts = base_filename.rsplit(".txt", 1)
        failure_review_filename = f"{filename_parts[0]}_{counter}.txt"
        failure_review_path = notifications_dir / failure_review_filename

    return failure_review_path


def analyze_single_failure_multi_query(
    failure_data: dict,
    llm,
    base_artifact_dir: Path,
    verbose: bool = False,
    generate_html: bool = True,
    failure_index: int = 0,
) -> dict:
    """
    Analyze a single failure using multiple focused LLM queries

    This approach breaks down the analysis into specific focused queries:
    1. Initial categorization and identification
    2. Root cause analysis
    3. Failed step breakdown
    4. Fix recommendations
    5. Synthetic summary
    6. Full technical analysis

    Args:
        failure_data: Dictionary with failure and execution log content
        llm: LangChain LLM client
        base_artifact_dir: Base artifact directory path for resolving file paths
        verbose: Whether to show verbose output during analysis
        generate_html: Whether to generate HTML report (default: True)

    Returns:
        Analysis result dictionary with all query results
    """
    logger.info(f"Multi-query analysis for: {failure_data['failure_dir']}")

    # Get list of available files
    all_files = list_all_files_in_artifact_dir(base_artifact_dir)
    relevant_files = []

    for file_path in all_files:
        file_lower = file_path.lower()
        if any(
            ext in file_lower
            for ext in [".yaml", ".yml", ".log", ".json", ".conf", ".cfg", ".txt", ".sh", ".py"]
        ):
            relevant_files.append(file_path)

    available_files = relevant_files[:20]

    try:
        # Track files being consumed
        investigated_files = []

        # Add initially consumed files
        if failure_data.get("failure_file"):
            investigated_files.append(failure_data["failure_file"])
        if failure_data.get("log_file") and failure_data["log_file"] != "No log file found":
            investigated_files.append(failure_data["log_file"])

        # Initialize query handler with failure data and available files
        queries_handler = FailureAnalysisQueries(failure_data, available_files)

        # Execute the full sequence of queries
        execution_result = execute_query_sequence(queries_handler, llm, verbose)

        analysis_results = execution_result["analysis_results"]
        queries_and_responses = execution_result["queries_and_responses"]

        # Check if any query requested additional files
        requested_files = []
        for resp in queries_and_responses:
            if "NEED_MORE_FILES:" in resp["response"]:
                # Extract requested files from response
                lines = resp["response"].split("\n")
                for line in lines:
                    if line.strip().startswith("NEED_MORE_FILES:"):
                        file_request = line.split(":", 1)[1].strip()
                        requested_files.append(file_request)

        if requested_files:
            logger.info(f"Queries requested additional files: {requested_files}")

        # Add file tracking information to each query response
        for resp in queries_and_responses:
            resp["files_available"] = available_files.copy()
            resp["files_consumed"] = investigated_files.copy()
            resp["files_requested"] = requested_files.copy()

        # Create structured analysis in the expected format
        structured_analysis = {
            "root_cause": analysis_results.get("root_cause", ""),
            "failed_step": analysis_results.get("failed_step", ""),
            "trigger": analysis_results.get("categorization", ""),
            "synthetic_summary": analysis_results.get("synthetic_summary", ""),
            "full_analysis": analysis_results.get("full_analysis", ""),
            "raw_analysis": "\n\n".join(
                [f"## {resp['query_type']}\n{resp['response']}" for resp in queries_and_responses]
            ),
        }

        result = {
            "status": "success",
            "failure_dir": failure_data["failure_dir"],
            "analysis": structured_analysis["full_analysis"],
            "structured_analysis": structured_analysis,
            "query_count": len(queries_and_responses),
            "analysis_results": analysis_results,
            "investigated_files": investigated_files,
            "files_available": available_files,
            "files_requested": requested_files,
        }

        # Generate FAILURE_REVIEW file from synthetic summary
        try:
            failure_review_content = structured_analysis.get("synthetic_summary", "")
            if failure_review_content:
                # Generate unique path for FAILURE_REVIEW file
                failure_dir_name = Path(failure_data["failure_dir"]).name
                failure_review_path = _generate_unique_failure_review_path(
                    base_artifact_dir, failure_dir_name
                )

                with open(failure_review_path, "w", encoding="utf-8") as f:
                    f.write(failure_review_content.strip())

                logger.info(f"📝 FAILURE_REVIEW generated: {failure_review_path}")
                result["failure_review_file"] = str(failure_review_path)
            else:
                logger.warning("No synthetic summary available for FAILURE_REVIEW generation")
        except Exception as e:
            logger.warning(f"Failed to generate FAILURE_REVIEW file: {e}")

        # Generate HTML report if enabled
        if generate_html or verbose:
            logger.info("📝 Generating HTML report...")
            try:
                html_path = generate_html_report(
                    queries_and_responses, base_artifact_dir, failure_data["failure_dir"]
                )
                logger.info(f"📄 Multi-query HTML report saved: {html_path}")
                result["html_report"] = html_path
            except Exception as e:
                logger.warning(f"Failed to generate HTML report: {e}")
                import traceback

                logger.debug(f"HTML generation traceback: {traceback.format_exc()}")
        else:
            logger.info(
                "ℹ️  HTML report generation disabled (set verbose=True or generate_html=True to enable)"
            )

        logger.info(
            f"✅ Multi-query analysis complete: {len(queries_and_responses)} queries executed"
        )
        return result

    except Exception as e:
        logger.error(f"❌ Multi-query analysis failed: {e}")

        # Try to generate HTML report even on failure if verbose mode and we have some data
        error_result = {
            "status": "error",
            "failure_dir": failure_data["failure_dir"],
            "error": str(e),
            "partial_results": locals().get("analysis_results", {}),
            "queries_completed": len(locals().get("queries_and_responses", [])),
            "investigated_files": locals().get("investigated_files", []),
            "files_available": locals().get("available_files", []),
            "files_requested": locals().get("requested_files", []),
        }

        # Try to generate FAILURE_REVIEW from partial results if available
        partial_analysis_results = locals().get("analysis_results", {})
        if partial_analysis_results.get("synthetic_summary"):
            try:
                failure_dir_name = Path(failure_data["failure_dir"]).name
                failure_review_path = _generate_unique_failure_review_path(
                    base_artifact_dir, failure_dir_name
                )

                with open(failure_review_path, "w", encoding="utf-8") as f:
                    f.write(partial_analysis_results["synthetic_summary"].strip())

                logger.info(
                    f"📝 Partial FAILURE_REVIEW generated despite failure: {failure_review_path}"
                )
                error_result["failure_review_file"] = str(failure_review_path)
            except Exception as review_error:
                logger.warning(f"Failed to generate partial FAILURE_REVIEW: {review_error}")

        if (
            (generate_html or verbose)
            and "queries_and_responses" in locals()
            and queries_and_responses
        ):
            try:
                html_path = generate_html_report(
                    queries_and_responses, base_artifact_dir, failure_data["failure_dir"]
                )
                logger.info(f"📄 Partial HTML report saved despite failure: {html_path}")
                error_result["html_report"] = html_path
            except Exception as html_error:
                logger.warning(f"Failed to generate HTML report for failed analysis: {html_error}")

        return error_result


def process_failure_analysis(base_artifact_dir: Path, llm, verbose: bool = False) -> dict:
    """
    Process failure analysis for the given artifact directory

    Args:
        base_artifact_dir: Path to the base artifact directory to analyze
        llm: Configured LangChain LLM client
        verbose: Whether to show verbose output

    Returns:
        Dictionary containing analysis results
    """
    logger.info(f"Processing failure analysis for: {base_artifact_dir}")

    if not base_artifact_dir.exists():
        raise FileNotFoundError(f"Artifact directory not found: {base_artifact_dir}")

    # Find all FAILURE files
    failure_files = find_failure_files(base_artifact_dir)

    if not failure_files:
        logger.warning("No FAILURE files found - bailing out")
        return {
            "artifact_dir": str(base_artifact_dir),
            "status": "success",
            "analysis": "No FAILURE files found in the artifact directory.",
            "failures_found": 0,
        }

    # Analyze each failure
    failure_analyses = []
    for index, failure_file in enumerate(failure_files):
        # Read failure and corresponding log file (task.log or _ansible.log)
        failure_data = read_failure_and_log(failure_file)

        # Analyze with LLM using multi-query approach
        analysis = analyze_single_failure_multi_query(
            failure_data, llm, base_artifact_dir, verbose, generate_html=True, failure_index=index
        )
        failure_analyses.append(analysis)

    # Combine all analyses
    successful_analyses = [a for a in failure_analyses if a["status"] == "success"]
    failed_analyses = [a for a in failure_analyses if a["status"] == "error"]

    result = {
        "artifact_dir": str(base_artifact_dir),
        "status": "success",
        "failures_found": len(failure_files),
        "analyses": failure_analyses,
        "successful_analyses": len(successful_analyses),
        "failed_analyses": len(failed_analyses),
        "model_used": llm.model_name if hasattr(llm, "model_name") else "unknown",
    }

    logger.info(
        f"✅ Analyzed {len(failure_files)} failures - {len(successful_analyses)} successful, {len(failed_analyses)} failed"
    )
    return result


def get_failure_explanations(base_artifact_dir: Path) -> list[dict]:
    """
    Get structured failure explanations only

    Args:
        base_artifact_dir: Path to the base artifact directory to analyze

    Returns:
        List of structured analysis dictionaries with root_cause, failed_step, etc.
    """
    result = run_on_failure_agent(base_artifact_dir, verbose=False)

    explanations = []
    if result.get("status") == "success" and result.get("analyses"):
        for analysis in result["analyses"]:
            if analysis.get("status") == "success" and analysis.get("structured_analysis"):
                explanation = analysis["structured_analysis"].copy()
                explanation["failure_dir"] = analysis["failure_dir"]
                explanation["investigated_files"] = analysis.get("investigated_files", [])
                explanation["failure_review_file"] = analysis.get("failure_review_file")
                explanations.append(explanation)

    return explanations


def run_on_failure_agent(base_artifact_dir: Path, verbose: bool = False) -> dict:
    """
    Main programmatic interface for the On Failure Agent

    This function can be called by other Python modules to trigger failure analysis.

    Args:
        base_artifact_dir: Path to the base artifact directory to analyze

    Returns:
        Dictionary containing analysis results
    """
    try:
        logger.info("🤖 On Failure Agent starting...")

        # Load model configuration from vault
        logger.info("Loading model configuration from vault...")
        models_config = load_model_config(
            vault_name="psap-models-corp-rh", content_name="agent-models.yaml"
        )

        model_config = models_config.get(MODEL_KEY)
        if not model_config:
            raise ValueError(f"Model key '{MODEL_KEY}' not found in the vault ...")

        logger.info(f"Loaded configuration for model: {model_config.get('model_id')}")

        # Create LangChain LLM client
        logger.info("Creating LangChain LLM client...")
        llm = create_llm_client(model_config)

        # Process the failure analysis
        result = process_failure_analysis(base_artifact_dir, llm, verbose)

        return result

    except Exception as e:
        logger.error(f"❌ Agent failed: {e}")
        return {"status": "error", "error": str(e)}
