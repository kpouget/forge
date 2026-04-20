"""
Logging utilities for the DSL framework
"""

import inspect
import logging
from pathlib import Path

import projects.core.library.env as env

LINE_WIDTH = 80


def setup_clean_logger(name: str):
    """Set up logger that shows only the message without prefix"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # Only configure if not already configured
    if not logger.handlers:
        # Create console handler with clean format
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(logging.Formatter("%(message)s"))

        logger.addHandler(console_handler)

    logger.propagate = False  # Don't propagate to root logger
    return logger


# Configure clean logging for DSL operations
logger = setup_clean_logger("DSL")


def log_task_header(task_name: str, task_doc: str, rel_filename: str, line_no: int):
    """Log the verbose task header with tildes"""
    logger.info("")
    logger.info("~" * LINE_WIDTH)
    logger.info(f"~~ {rel_filename}:{line_no}")
    logger.info(f"~~ TASK: {task_name} : {task_doc or 'No description'}")
    logger.info("~" * LINE_WIDTH)
    logger.info("")


def log_execution_banner(function_args: dict = None, log_file: str = None):
    """Log the execution banner with function info and arguments"""
    # Get the caller's filename and function name for the header
    frame = inspect.currentframe()
    caller_frame = (
        frame.f_back.f_back
    )  # Go back 2 frames (this func -> execute_tasks -> actual caller)
    filename = caller_frame.f_code.co_filename

    rel_filename = _get_forge_relative_path(filename)

    # Use parent directory name as function name for toolbox operations
    function_name = _get_toolbox_function_name(filename)

    # Print execution header
    logger.info("")
    logger.info("===============================================================================")
    logger.info(f"| FILE: {rel_filename}")
    logger.info(f"| COMMAND: {function_name}")

    if function_args:
        # Display arguments in YAML format
        logger.info("| ARGUMENTS:")

        for key, value in function_args.items():
            if key == "function_args":  # Skip the function_args parameter itself
                continue
            if value is None:
                continue

            logger.info(f"|   {key}: {value}")

    logger.info(f"| ARTIFACT_DIR: {env.ARTIFACT_DIR}")
    logger.info(f"| LOG_FILE: {log_file}")
    logger.info("===============================================================================")
    logger.info("")


def log_completion_banner(function_args: dict = None, status: str = "SUCCESS"):
    """Log the completion banner with function info and completion status"""
    # Get the caller's filename and function name for the header
    frame = inspect.currentframe()
    caller_frame = (
        frame.f_back.f_back
    )  # Go back 2 frames (this func -> execute_tasks -> actual caller)
    filename = caller_frame.f_code.co_filename

    rel_filename = _get_forge_relative_path(filename)

    # Use parent directory name as function name for toolbox operations
    function_name = _get_toolbox_function_name(filename)

    # Print completion header
    logger.info("")
    logger.info("===============================================================================")
    logger.info(f"| {rel_filename}")
    logger.info(f"| STATUS: {status}")
    logger.info(f"| COMMAND: {function_name}")
    logger.info(f"| ARTIFACTS: {env.ARTIFACT_DIR}")
    logger.info("===============================================================================")
    logger.info("")


def _get_forge_relative_path(filename):
    """Get file path relative to FORGE home directory (forge root)"""
    filename_path = Path(filename)

    return filename_path.relative_to(env.FORGE_HOME)


def _get_toolbox_function_name(filename):
    """Extract toolbox function name from file path (parent directory name)"""
    filename_path = Path(filename)

    # For paths like projects/llm_d/toolbox/capture_llmisvc_state/main.py
    # Return the parent directory name: capture_llmisvc_state
    return filename_path.parent.name
