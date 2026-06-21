# Llama Stack Performance Tests

FORGE project for running performance benchmarks against Llama Stack (RHOAI operator-managed distributions).

## Architecture

```
projects/
├── agentic_tools/                     # Shared toolbox
│   ├── locust/
│   │   ├── locust_users/             # User classes (one per file)
│   │   ├── locust_runtime/           # Pod-mounted scripts (entrypoint, shapes, hooks)
│   │   ├── templates/                # K8s job template (locust_job.yaml)
│   │   └── toolbox/
│   │       ├── run_distributed/      # Deploy Locust jobs, wait, collect CSV
│   │       ├── parse_results/        # Parse Locust CSV into RunMetrics
│   │       ├── export_to_mlflow/     # Generate summary + export metrics
│   │       └── generate_prompts/     # Tokenizer-accurate synthetic prompts
│   ├── mcp/
│   │   └── toolbox/
│   │       └── deploy_tokenized_mcp_server/
│   └── utils/
│       └── token_text.py             # Shared tokenizer encode-trim-decode
│
├── llamastack/                        # This project
│   ├── orchestration/
│   │   ├── ci.py                     # CLI phases (prepare, test, pre-cleanup, post-cleanup)
│   │   ├── runtime_config.py         # Config accessors + build_locust_config()
│   │   ├── prepare_phase.py          # Deploy vLLM inference service
│   │   ├── test_phase.py             # Test matrix execution
│   │   ├── cleanup_phase.py          # Resource cleanup (pre/post)
│   │   ├── manifests/                # K8s manifests (LlamaStack, Postgres, RHAIIS)
│   │   ├── config.d/                 # Layered config
│   │   └── presets.d/                # Named presets
│   └── toolbox/
│       ├── deploy_llamastack/        # LlamaStackDistribution CR deployment
│       ├── deploy_rhaiis/            # RHAIIS/vLLM InferenceService
│       ├── deploy_postgres/          # PostgreSQL for LlamaStack state
│       └── cleanup_test_resources/   # Test resource cleanup
```

## Experiment Types

| Type | Description | Deploys | Host URL |
|------|-------------|---------|----------|
| `lls_overhead` | LlamaStack routing overhead measurement | Postgres + LlamaStack + vLLM | LlamaStack :8321 |
| `rhaiis_direct` | Direct vLLM/RHAIIS baseline | vLLM only | vLLM predictor :8080 |

## Presets

| Preset | Type | Description |
|--------|------|-------------|
| `smoke` | lls_overhead | Quick 30s validation, 10 users, 1 replica |
| `overhead-simple` | lls_overhead | 100 users, 30s, 50 tokens |
| `overhead-sweep` | lls_overhead | Replicas [1,2,4] × concurrency [1..128] |
| `test-sweep` | lls_overhead | Small sweep for testing (replicas [1,2] × [64,128]) |
| `overhead-4r-128u` | lls_overhead | Single test: 4 replicas, 128 users |
| `direct-128u` | rhaiis_direct | Direct vLLM baseline: 128 users |
| `mcp-benchmark` | lls_overhead | Deterministic MCP tool-calling |

## Usage

```bash
# Set preset and run
export FORGE_PRESET=smoke
python -m projects.llamastack.orchestration.ci prepare
python -m projects.llamastack.orchestration.ci test
python -m projects.llamastack.orchestration.ci pre-cleanup

# Sweep (same command — test auto-detects matrix from preset)
export FORGE_PRESET=overhead-sweep
python -m projects.llamastack.orchestration.ci prepare
python -m projects.llamastack.orchestration.ci test
python -m projects.llamastack.orchestration.ci post-cleanup

# MLflow export (set vault path first)
export PSAP_FORGE_MLFLOW_EXPORT_SECRET_PATH=$(pwd)/vaults/psap-forge-mlflow-export
```
