"""
FORGE Failure Analysis Queries

This module contains all the query templates and logic for the multi-query
failure analysis approach. Each query is focused on a specific aspect of
failure investigation.
"""

import time
import warnings
from datetime import datetime
from typing import Any

import urllib3


class FailureAnalysisQueries:
    """Container for all failure analysis query templates and logic"""

    def __init__(self, failure_data: dict[str, Any], available_files: list[str]):
        """
        Initialize with failure context

        Args:
            failure_data: Dictionary containing failure information
            available_files: List of available files in artifact directory
        """
        self.failure_content = failure_data["failure_content"]
        self.log_content = failure_data.get("log_content") or failure_data.get(
            "ansible_content", ""
        )
        self.failed_tasks_section = ""
        if failure_data.get("failed_tasks_summary"):
            self.failed_tasks_section = f"""
## FAILED TASKS IDENTIFIED:
{failure_data["failed_tasks_summary"]}
"""
        self.available_files = available_files[:10]  # Limit for prompt size

    def build_common_context(self, log_excerpt_length: int = None) -> str:
        """Build the common failure and execution log context for queries"""
        log_excerpt = self.log_content
        if log_excerpt_length:
            log_excerpt = f"{self.log_content[:log_excerpt_length]}..."

        return f"""
## FAILURE:
```
{self.failure_content}
{self.failed_tasks_section}
```

## EXECUTION LOG:
```
{log_excerpt}
```
"""

    def build_failure_context(self) -> str:
        """Build just the failure context without execution log"""
        return f"""
## FAILURE:
```
{self.failure_content}
{self.failed_tasks_section}
```
"""

    def query_categorization(self) -> dict[str, str]:
        """Initial categorization and identification"""
        return {
            "type": "Initial Categorization",
            "content": f"""
Analyze this FORGE test failure for initial categorization.
{self.build_common_context(log_excerpt_length=2000)}
## TASK:
Provide a brief initial categorization based ONLY on the actual failure content and logs above:

1. **Failure Type**: Based on what is actually shown in the logs
2. **Severity**: Based on the actual impact observed
3. **Component**: Which specific system/component failed according to the logs
4. **Quick Summary**: 1-2 sentences describing what actually happened

IMPORTANT: Use ONLY information from the logs above. Do NOT speculate or invent generic possibilities.
""",
        }

    def query_root_cause(self) -> dict[str, str]:
        """Root cause analysis"""
        return {
            "type": "Root Cause Analysis",
            "content": f"""
Based on this FORGE test failure, provide a deep technical root cause analysis.
{self.build_common_context()}
## TASK:
Perform deep root cause analysis using ONLY the actual evidence from the logs:

**Root Cause**: Identify the fundamental technical reason for the failure based on what is actually shown in the execution log and failure message above.

IMPORTANT:
- Use ONLY specific evidence from the logs - exact error messages, exit codes, file paths, command outputs
- Do NOT speculate about possible causes that aren't shown in the logs
- Do NOT create generic lists of potential issues
- Quote specific log entries that support your analysis
""",
        }

    def query_failed_step(self) -> dict[str, str]:
        """Failed step breakdown"""
        return {
            "type": "Failed Step Breakdown",
            "content": f"""
Analyze the specific step that failed in this FORGE test.
{self.build_common_context()}
## TASK:
Identify the exact step that failed based on the actual log content:

**Failed Step**: Find the specific step/task/operation that failed by looking for:
- In DSL logs: "==> TASK FAILED:" patterns
- In execution logs: "----- FAILED ----" patterns
- Specific commands that returned error codes
- Operations that timed out or were interrupted

Provide the exact step name from the logs, what it was trying to do, and the immediate error message.

IMPORTANT: Only report what is actually shown in the logs above. Do NOT invent or assume failure scenarios.
""",
        }

    def query_synthetic_summary(self, analysis_results: dict[str, str]) -> dict[str, str]:
        """Synthetic summary"""
        return {
            "type": "Synthetic Summary",
            "content": f"""
Create a concise executive summary of this FORGE test failure.

## ALL ANALYSIS RESULTS:

- **Categorization**: {analysis_results.get("categorization", "")}

- **Root Cause**: {analysis_results.get("root_cause", "")}

- **Failed Step**: {analysis_results.get("failed_step", "")}

## TASK:
Create a brief executive summary (2-3 sentences) based on the analysis above that captures:
- What failed
- Why it failed

Use ONLY the information from the analysis results above. Do NOT add speculations or generic advice.
""",
        }

    def query_full_analysis(self, analysis_results: dict[str, str]) -> dict[str, str]:
        """Full technical analysis"""
        return {
            "type": "Full Technical Analysis",
            "content": f"""
Create a comprehensive technical analysis document for this FORGE test failure.

## ALL ANALYSIS RESULTS:
{chr(10).join([f"**{k.title()}**: {v}" for k, v in analysis_results.items()])}

## TASK:
Create a detailed technical analysis document that combines all findings into a coherent narrative. Include:

1. **Overview**: What happened and impact
2. **Technical Details**: Deep dive into the failure mechanism
3. **Timeline**: Sequence of events leading to the failure

Focus on understanding and documenting what actually occurred based on the evidence gathered.
Do NOT add speculative solutions or generic prevention strategies.
""",
        }

    def get_all_queries(self) -> list[dict[str, Any]]:
        """
        Get all queries in execution order.

        This method handles the sequential execution of queries where later
        queries depend on results from earlier ones.

        Returns:
            List of query dictionaries with 'query_func', 'dependencies', and 'result_key'
        """
        return [
            {
                "query_func": self.query_categorization,
                "dependencies": [],
                "result_key": "categorization",
                "description": "Initial failure categorization",
            },
            {
                "query_func": self.query_root_cause,
                "dependencies": [],
                "result_key": "root_cause",
                "description": "Deep technical root cause analysis",
            },
            {
                "query_func": self.query_failed_step,
                "dependencies": [],
                "result_key": "failed_step",
                "description": "Exact failed step identification",
            },
            {
                "query_func": self.query_synthetic_summary,
                "dependencies": ["categorization", "root_cause", "failed_step"],
                "result_key": "synthetic_summary",
                "description": "Executive summary",
            },
            {
                "query_func": self.query_full_analysis,
                "dependencies": ["categorization", "root_cause", "failed_step"],
                "result_key": "full_analysis",
                "description": "Comprehensive technical analysis",
            },
        ]


def execute_query_sequence(
    queries_handler: FailureAnalysisQueries, llm, verbose: bool = False
) -> dict[str, Any]:
    """
    Execute the full sequence of failure analysis queries.

    Args:
        queries_handler: Initialized FailureAnalysisQueries instance
        llm: LangChain LLM client
        verbose: Whether to show verbose output

    Returns:
        Dictionary containing all analysis results and tracking information
    """
    from langchain_core.messages import HumanMessage

    analysis_results = {}
    queries_and_responses = []

    query_sequence = queries_handler.get_all_queries()

    for i, query_def in enumerate(query_sequence, 1):
        query_func = query_def["query_func"]
        dependencies = query_def["dependencies"]
        result_key = query_def["result_key"]
        description = query_def["description"]

        if verbose:
            print(f"\n🤖 QUERY {i} - {query_def['query_func'].__name__.upper().replace('_', ' ')}")

        # Build query with dependencies
        if dependencies:
            # Check if this is a method that expects the full analysis_results dict
            # (synthetic_summary and full_analysis expect the full dict, others expect individual args)
            if query_func.__name__ in ["query_synthetic_summary", "query_full_analysis"]:
                # Pass the full analysis_results dict
                query_data = query_func(analysis_results)
            else:
                # Pass individual dependency values as separate arguments
                dep_args = [analysis_results[dep] for dep in dependencies]
                query_data = query_func(*dep_args)
        else:
            # No dependencies, call without arguments
            query_data = query_func()

        query_type = query_data["type"]
        query_content = query_data["content"]

        # Execute query with timing and suppress SSL warnings for internal endpoints
        start_time = time.time()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)
            response = llm.invoke([HumanMessage(content=query_content)])
        end_time = time.time()

        response_content = response.content.strip()

        # Extract token usage and other metadata from response
        response_metadata = getattr(response, "response_metadata", {})
        token_usage = response_metadata.get("token_usage", {})

        processing_time = end_time - start_time
        prompt_tokens = token_usage.get("prompt_tokens", 0)
        completion_tokens = token_usage.get("completion_tokens", 0)
        total_tokens = token_usage.get("total_tokens", prompt_tokens + completion_tokens)

        # Store result
        analysis_results[result_key] = response_content

        # Track for HTML report with enhanced metadata
        queries_and_responses.append(
            {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "query_type": query_type,
                "query": query_content,
                "response": response_content,
                "processing_time": processing_time,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "prompt_length": len(query_content),
                "response_length": len(response_content),
            }
        )

        if verbose:
            print(f"✅ {description}: {response_content[:100]}...")

    return {
        "analysis_results": analysis_results,
        "queries_and_responses": queries_and_responses,
        "query_count": len(query_sequence),
    }
