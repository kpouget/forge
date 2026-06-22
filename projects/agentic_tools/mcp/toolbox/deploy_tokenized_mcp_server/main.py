"""
Deploy a tokenizer-accurate MCP Server for deterministic load testing.

This server provides:
- N configurable tools (tool_0, tool_1, ...) with zero processing overhead
- Each tool returns tokenizer-accurate random text of exactly TOOL_RESPONSE_TOKENS tokens
- Optional tokenizer-accurate tool descriptions of TOOL_DESCRIPTION_TOKENS tokens
- Pre-generated response pool for sub-millisecond tool call latency
- Health endpoint at /health

Reusable across any FORGE project that needs deterministic MCP tool responses
(e.g. LlamaStack benchmarks, MCP Gateway benchmarks).

Configuration is via environment variables on the Deployment:
    NUM_TOOLS              - Number of tools to register (default: 1)
    TOOL_RESPONSE_TOKENS   - Exact token count per tool response (default: 100)
    TOOL_DESCRIPTION_TOKENS - Exact token count for tool descriptions (default: 0)
    TOKENIZER_MODEL        - HuggingFace model for tokenizer (default: Qwen/Qwen3-VL-30B-A3B-Instruct)
    POOL_SIZE              - Pre-generated response pool size (default: 50)
    PORT                   - Server port (default: 8000)
"""

from __future__ import annotations

import logging

from projects.core.dsl import entrypoint, execute_tasks, retry, task
from projects.core.dsl.utils.k8s import oc, oc_get_json
from projects.core.library import env

logger = logging.getLogger(__name__)

DEFAULT_IMAGE = "quay.io/rh-ee-ashtarkb/mock_mcp:latest"


@entrypoint
def run(
    *,
    namespace: str,
    name: str = "benchmark-mcp-server",
    image: str = DEFAULT_IMAGE,
    num_tools: int = 1,
    tool_response_tokens: int = 100,
    tool_description_tokens: int = 0,
    tokenizer_model: str = "Qwen/Qwen3-VL-30B-A3B-Instruct",
    pool_size: int = 50,
    labels: dict[str, str] | None = None,
    rollout_timeout: str = "300s",
) -> int:
    """
    Deploy a tokenizer-accurate MCP Server with configurable responses.

    Args:
        namespace: Target namespace
        name: Deployment name
        image: Container image for the server
        num_tools: Number of tools to register
        tool_response_tokens: Exact tokens per tool response
        tool_description_tokens: Exact tokens per tool description (0=default)
        tokenizer_model: HuggingFace model name for tokenizer
        pool_size: Number of pre-generated responses
        labels: Additional labels for the deployment
        rollout_timeout: Timeout for rollout status wait
    """
    execute_tasks(locals())
    return 0


def configure(
    *,
    namespace: str,
    name: str = "benchmark-mcp-server",
    num_tools: int | None = None,
    tool_response_tokens: int | None = None,
    tool_description_tokens: int | None = None,
    tokenizer_model: str | None = None,
    pool_size: int | None = None,
) -> None:
    """Reconfigure an existing server without redeploying.

    Only sets env vars that are explicitly provided (non-None).
    Triggers a rollout restart for the new config to take effect.
    """
    env_updates = {}
    if num_tools is not None:
        env_updates["NUM_TOOLS"] = str(num_tools)
    if tool_response_tokens is not None:
        env_updates["TOOL_RESPONSE_TOKENS"] = str(tool_response_tokens)
    if tool_description_tokens is not None:
        env_updates["TOOL_DESCRIPTION_TOKENS"] = str(tool_description_tokens)
    if tokenizer_model is not None:
        env_updates["TOKENIZER_MODEL"] = tokenizer_model
    if pool_size is not None:
        env_updates["POOL_SIZE"] = str(pool_size)

    if not env_updates:
        logger.info("No configuration changes specified")
        return

    env_args = [f"{k}={v}" for k, v in env_updates.items()]
    logger.info("Reconfiguring %s: %s", name, env_args)
    oc("set", "env", f"deployment/{name}", "-n", namespace, *env_args, check=True)
    oc("rollout", "restart", f"deployment/{name}", "-n", namespace, check=True)
    oc("rollout", "status", f"deployment/{name}", "-n", namespace, f"--timeout={120}s", check=True)


@task
def deploy_server(args, ctx):
    """Apply tokenized MCP Server deployment and service."""
    labels = args.labels or {}
    labels.setdefault("app", args.name)
    labels.setdefault("forge.openshift.io/component", "tokenized-mcp-server")

    label_str = "\n    ".join(f'{k}: "{v}"' for k, v in labels.items())
    selector_labels = f"app: {args.name}"

    manifest = f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: {args.name}
  labels:
    {label_str}
spec:
  replicas: 1
  selector:
    matchLabels:
      {selector_labels}
  template:
    metadata:
      labels:
        {selector_labels}
    spec:
      containers:
        - name: mcp-server
          image: {args.image}
          ports:
            - containerPort: 8000
          env:
            - name: NUM_TOOLS
              value: "{args.num_tools}"
            - name: TOOL_RESPONSE_TOKENS
              value: "{args.tool_response_tokens}"
            - name: TOOL_DESCRIPTION_TOKENS
              value: "{args.tool_description_tokens}"
            - name: TOKENIZER_MODEL
              value: "{args.tokenizer_model}"
            - name: POOL_SIZE
              value: "{args.pool_size}"
            - name: PORT
              value: "8000"
          resources:
            requests:
              memory: "512Mi"
              cpu: "250m"
            limits:
              memory: "2Gi"
              cpu: "1"
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 30
            periodSeconds: 10
---
apiVersion: v1
kind: Service
metadata:
  name: {args.name}
  labels:
    {label_str}
spec:
  selector:
    {selector_labels}
  ports:
    - port: 8000
      targetPort: 8000
"""

    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(manifest)
        manifest_path = f.name

    oc("apply", "-f", manifest_path, "-n", args.namespace, check=True)

    import os

    os.unlink(manifest_path)

    artifacts_src = env.ARTIFACT_DIR / "src"
    artifacts_src.mkdir(parents=True, exist_ok=True)
    (artifacts_src / f"{args.name}-deployment.yaml").write_text(manifest, encoding="utf-8")

    return f"Tokenized MCP Server '{args.name}' deployed (tools={args.num_tools}, response_tokens={args.tool_response_tokens})"


@retry(attempts=30, delay=10, backoff=1.0)
@task
def wait_for_ready(args, ctx):
    """Wait for the server to be ready (tokenizer download + pool generation)."""
    payload = oc_get_json(
        "deployment",
        name=args.name,
        namespace=args.namespace,
        ignore_not_found=True,
    )
    if not payload:
        return (False, f"Waiting for deployment {args.name}")

    available = payload.get("status", {}).get("availableReplicas", 0)
    if available > 0:
        return f"Tokenized MCP Server '{args.name}' is ready"

    return (False, f"Waiting for {args.name} replicas (tokenizer loading...)")
