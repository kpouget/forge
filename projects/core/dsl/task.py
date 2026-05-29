"""
Task decorator and retry functionality
"""

import functools
import inspect
import logging
import os
import time

from .log import log_task_header
from .script_manager import get_script_manager

LINE_WIDTH = 80


logger = logging.getLogger("DSL")
logger.propagate = False  # Don't show logger prefix


class ConditionError(Exception):
    pass


class RetryFailure(Exception):
    pass


def _ensure_is_task(func, decorator_name):
    """
    Validate that a function is already decorated with @task.

    Args:
        func: The function to check
        decorator_name: Name of the decorator calling this (for error messages)

    Raises:
        TypeError: If the function is not a task
    """

    if not hasattr(func, "is_dsl_task") or not func.is_dsl_task:
        raise TypeError(
            f"@{decorator_name} can only be applied to functions decorated with @task. \n"
            f"Function '{func.__name__}' is not a task. \n"
            f"Put '@task' BELOW '@{decorator_name}' in your decorator stack."
        )
    return True


def _execute_with_retry(func, attempts, delay, backoff, retry_on_exceptions, *args, **kwargs):
    """
    Execute a function with retry logic.

    Args:
        func: The function to execute
        attempts: Number of retry attempts
        delay: Initial delay between retries in seconds
        backoff: Multiplier for delay on each retry
        retry_on_exceptions: If True, retry on raised exceptions (never on KeyboardInterrupt)
        *args, **kwargs: Arguments to pass to the function

    Returns:
        Result of the function execution

    Raises:
        RetryFailure: If all retry attempts fail
    """
    retry_config = getattr(func, "_retry_config", {})
    retry_attempts = retry_config.get("attempts", attempts)
    retry_delay = retry_config.get("delay", delay)
    retry_backoff = retry_config.get("backoff", backoff)
    retry_on_exc = retry_config.get("retry_on_exceptions", retry_on_exceptions)

    current_delay = retry_delay
    start_time = time.time()  # Track when retry attempts started

    for attempt in range(retry_attempts):
        try:
            result = func(*args, **kwargs)

            # Check if result indicates we should retry (falsy values like False, None, [], etc.)
            if not result:
                if attempt < retry_attempts - 1:  # Not the last attempt
                    elapsed_time = time.time() - start_time
                    elapsed_mins, elapsed_secs = divmod(elapsed_time, 60)
                    logger.info("")
                    logger.info("~" * LINE_WIDTH)
                    logger.info(f"~~ TASK: {func.__name__} : {func.__doc__ or 'No description'}")
                    logger.warning(
                        f"~~ RETRY ATTEMPT #{attempt + 1}/{retry_attempts} (returned: {result})"
                    )
                    logger.info(f"~~ ELAPSED TIME: {elapsed_mins:.0f}m {elapsed_secs:.0f}s")
                    logger.info(f"~~ RETRY in {current_delay:.0f}s")
                    logger.info("~" * LINE_WIDTH)
                    time.sleep(current_delay)
                    logger.info("")

                    current_delay *= retry_backoff
                else:
                    elapsed_time = time.time() - start_time
                    elapsed_mins, elapsed_secs = divmod(elapsed_time, 60)
                    logger.error(
                        f"==> ALL ATTEMPTS FAILED: {retry_attempts}/{retry_attempts} after {elapsed_mins:.0f}m {elapsed_secs:.0f}s"
                    )
                    logger.info("")
                    raise RetryFailure(
                        f"All {retry_attempts} attempts failed for task {func.__name__} : {func.__doc__ or 'No description'} (last result: {result})"
                    )
            else:
                # Truthy result means success
                return result

        except KeyboardInterrupt:
            # Don't retry on keyboard interrupt, just re-raise immediately
            raise
        except Exception as exc:
            if not retry_on_exc:
                logger.error(f"==> TASK EXCEPTION: {func.__name__} failed with exception")
                logger.info("")
                raise exc

            if attempt >= retry_attempts - 1:
                elapsed_time = time.time() - start_time
                elapsed_mins, elapsed_secs = divmod(elapsed_time, 60)
                logger.error(
                    f"==> ALL ATTEMPTS FAILED: {retry_attempts}/{retry_attempts} after {elapsed_mins:.0f}m {elapsed_secs:.0f}s"
                )
                logger.info("")
                raise RetryFailure(
                    f"All {retry_attempts} attempts failed for task {func.__name__} : "
                    f"{func.__doc__ or 'No description'} (last error: {exc.__class__.__name__}: {exc})"
                ) from exc

            elapsed_time = time.time() - start_time
            elapsed_mins, elapsed_secs = divmod(elapsed_time, 60)
            logger.info("")
            logger.info("~" * LINE_WIDTH)
            logger.info(f"~~ TASK: {func.__name__} : {func.__doc__ or 'No description'}")
            logger.warning(
                f"~~ RETRY ATTEMPT #{attempt + 1}/{retry_attempts} "
                f"({exc.__class__.__name__}: {exc})"
            )
            logger.info(f"~~ ELAPSED TIME: {elapsed_mins:.0f}m {elapsed_secs:.0f}s")
            logger.info(f"~~ RETRY in {current_delay:.0f}s")
            logger.info("~" * LINE_WIDTH)
            time.sleep(current_delay)
            logger.info("")
            current_delay *= retry_backoff

    raise RetryFailure(
        f"All {retry_attempts} attempts failed for task {func.__name__} : "
        f"{func.__doc__ or 'No description'} (exceptions retried until exhausted)"
    )


def task_only(decorator_func):
    """
    Decorator for decorator functions that should only be applied to @task functions.

    This ensures that decorators like @always, @when, @retry can only be applied
    to functions that are already decorated with @task.

    Handles both simple decorators and decorator factories:

    Simple decorator usage (single parameter must be named ``func`` so factories
    like ``when(condition)`` are not mistaken for ``@decorator`` on a lambda):

        @task_only
        def always(func):
            func._always_execute = True
            return func

    Decorator factory usage:
        @task_only
        def retry(attempts=3, delay=1):
            def decorator(func):
                # decorator logic here
                return func
            return decorator
    """

    @functools.wraps(decorator_func)
    def wrapper(*args, **kwargs):
        # Use the signature of decorator_func to determine if it's a simple decorator or factory
        sig = inspect.signature(decorator_func)
        params = list(sig.parameters.values())

        # Simple decorator case: @always(func) — single parameter must be named like
        # the wrapped callable (e.g. "func"), not a factory argument such as "condition".
        if (
            len(params) == 1
            and params[0].name == "func"
            and len(args) == 1
            and len(kwargs) == 0
            and callable(args[0])
            and hasattr(args[0], "__name__")
        ):
            func = args[0]
            _ensure_is_task(func, decorator_func.__name__)

            return decorator_func(func)
        else:
            # Decorator factory case: @retry(attempts=3) or @when(condition)
            # Return a decorator that validates when applied to a function
            def inner_decorator(func):
                _ensure_is_task(func, decorator_func.__name__)
                # Call the original decorator factory with the parameters,
                # then apply the resulting decorator to the function
                actual_decorator = decorator_func(*args, **kwargs)
                return actual_decorator(func)

            return inner_decorator

    return wrapper


# TaskResult class moved to script_manager.py


def task(func):
    """
    Mark a function as a DSL task and register it
    """
    # Capture file and line info at definition time, not execution time
    frame = inspect.currentframe()
    caller_frame = frame.f_back
    definition_filename = caller_frame.f_code.co_filename
    definition_line_no = caller_frame.f_lineno

    # Make filename relative to current working directory
    try:
        rel_definition_filename = os.path.relpath(definition_filename)
    except ValueError:
        rel_definition_filename = definition_filename

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        task_name = func.__name__

        # Log task header using definition location
        log_task_header(task_name, func.__doc__, rel_definition_filename, definition_line_no)

        try:
            result = func(*args, **kwargs)
            # Store result for conditional execution
            script_manager = get_script_manager()
            task_id = wrapper._task_info["id"]
            task_result = script_manager.get_task_result(task_id)
            if task_result:
                task_result._set_result(result)
            return result
        except KeyboardInterrupt:
            raise
        except Exception as e:
            logger.error(f"==> TASK FAILED: {task_name}: {func.__doc__ or 'No description'}")
            logger.error(f"==> {e.__class__.__name__}: {e}")
            logger.info("")
            raise

    # Mark the function as a task
    wrapper.is_dsl_task = True
    wrapper.task_name = func.__name__
    wrapper.original_func = func

    # Register the task with the script manager
    task_info = {
        "id": f"{rel_definition_filename}:{definition_line_no}",
        "name": func.__name__,
        "func": wrapper,
        "condition": getattr(func, "_when_condition", None),
        "retry_config": getattr(func, "_retry_config", None),  # May be updated by @retry
        "always_execute": getattr(func, "_always_execute", False),
    }

    script_manager = get_script_manager()
    script_manager.register_task(task_info, rel_definition_filename)

    # Store reference to task_info so other decorators can update it
    wrapper._task_info = task_info

    # Make the result accessible as an attribute of the function
    wrapper.status = script_manager.get_task_result(task_info["id"])

    return wrapper


@task_only
def when(condition):
    """
    Conditional execution decorator with lazy evaluation

    Must be applied to a function that is already decorated with @task.

    Args:
        condition: A callable (lambda) that returns True/False
                  Use lambda for lazy evaluation: @when(lambda: some_task.status.return_value is True)

    Examples:
        @when(lambda: check_existing_service.status.return_value is True)
        @when(lambda: some_variable > 5)
        @when(lambda: os.path.exists("/tmp/flag"))
    """

    def decorator(func):
        func._when_condition = condition
        if hasattr(func, "_task_info"):
            func._task_info["condition"] = condition
        return func

    return decorator


def always(func):
    """
    Mark a task to always execute, even if previous tasks fail

    Can be applied before or after @task decorator.
    """
    func._always_execute = True

    # If this is already a registered task, update its always_execute flag
    if hasattr(func, "_task_info"):
        func._task_info["always_execute"] = True

    return func


@task_only
def retry(attempts=3, delay=1, backoff=1.0, retry_on_exceptions=False):
    """
    Retry decorator for @task functions.

    Must be applied to a function that is already decorated with @task.

    Args:
        attempts: Number of retry attempts
        delay: Initial delay between retries in seconds
        backoff: Multiplier for delay on each retry
        retry_on_exceptions: If True, also retry when the task raises (never on KeyboardInterrupt)
    """

    def decorator(func):
        # Store retry config on function (runtime will handle the actual retry)
        retry_config = {
            "attempts": attempts,
            "delay": delay,
            "backoff": backoff,
            "retry_on_exceptions": retry_on_exceptions,
        }
        func._retry_config = retry_config

        # If this is already a registered task, update its retry config
        if hasattr(func, "_task_info"):
            func._task_info["retry_config"] = retry_config

        return func

    return decorator


def entrypoint(func):
    """
    Mark a function as a DSL entrypoint, automatically adding artifact directory parameters
    and creating a main() function for CLI execution.

    Automatically injects artifact_dirname_suffix and artifact_dirname_prefix parameters
    to the function signature and creates a main() function accessible as func.main().

    Usage:
        @entrypoint
        def run(project: str, cluster_name: str):
            # Function will automatically accept artifact_dirname_suffix and artifact_dirname_prefix
            pass

        if __name__ == "__main__":
            run.main()
    """
    # Get the original function signature
    sig = inspect.signature(func)

    # Add the artifact directory parameters to the signature
    new_params = list(sig.parameters.values())

    # Add suffix parameter
    suffix_param = inspect.Parameter(
        "artifact_dirname_suffix", inspect.Parameter.KEYWORD_ONLY, default=None, annotation=str
    )
    new_params.append(suffix_param)

    # Add prefix parameter
    prefix_param = inspect.Parameter(
        "artifact_dirname_prefix", inspect.Parameter.KEYWORD_ONLY, default=None, annotation=str
    )
    new_params.append(prefix_param)

    # Create new signature
    new_sig = sig.replace(parameters=new_params)

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # Get the original function's parameter names
        orig_sig = inspect.signature(func)
        orig_param_names = set(orig_sig.parameters.keys())

        # Split kwargs into original function params and DSL runtime params
        func_kwargs = {}
        dsl_kwargs = {}

        for key, value in kwargs.items():
            if key in orig_param_names:
                func_kwargs[key] = value
            else:
                dsl_kwargs[key] = value

        # Store DSL parameters for runtime access
        wrapper._dsl_runtime_params = dsl_kwargs

        return func(*args, **func_kwargs)

    # Set the new signature on the wrapper
    wrapper.__signature__ = new_sig

    # Create main function for CLI execution
    def main():
        """CLI entrypoint with dynamic argument discovery"""
        from . import toolbox

        toolbox.run_toolbox_command(wrapper)

    # Attach main function to the wrapper
    wrapper.main = main

    return wrapper
