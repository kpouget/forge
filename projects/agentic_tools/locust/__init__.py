"""
Locust Distributed Load Testing Toolbox

Shared infrastructure for running distributed Locust load tests on Kubernetes.
Handles Job templating, deployment, result collection, and cleanup.

Submodules:
    locust_runtime/
      locustfile_main.py  - Entry point (activates user class by USER_CLASS env var)
      metrics_hook.py     - Warmup stats-reset hook
      locust_shapes.py    - Load shape definitions (steady, spike, realistic, poisson, custom)
    locust_users/
      Individual Locust user class files (one class per file):
        responses_simple_user.py, responses_mcp_user.py,
        responses_mcp_benchmark_user.py, chat_completions_user.py,
        mcp_session_user.py
    helpers/
      parse_results.py   - Parse Locust CSV/JSON results into RunMetrics
      summary.py         - Save metrics.json + parameters.json for caliper multi-run export
    toolbox/
      run_distributed/   - Deploy and manage distributed Locust K8s Jobs
      generate_prompts/  - Tokenizer-accurate synthetic prompt generation
    templates/
      locust_job.yaml    - Generic distributed Locust K8s Job template
"""
