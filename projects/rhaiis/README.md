# rhaiis

`rhaiis` is the Forge project for benchmarking AI inference engines on OpenShift
using KServe InferenceService.

The current implementation focuses on single-replica vLLM deployments benchmarked
with GuideLLM. The workflow deploys an InferenceService, waits for readiness,
runs a GuideLLM benchmark job, extracts results, and cleans up.

## Workflow sequence

```
deploy_kserve_isvc      Deploy ServingRuntime + InferenceService
        |
wait_isvc_ready         Poll InferenceService status + health check
        |
run_guidellm_benchmark  Create PVC, run GuideLLM job, copy results, cleanup
        |
capture_isvc_state      Capture ISVC YAML, pod logs, events
        |
cleanup_isvc            Delete InferenceService + ServingRuntime
```

On failure at any step, `capture_isvc_state` and `cleanup_isvc` still run
(try/finally in the orchestration layer).

Inside `run_guidellm_benchmark`, the task sequence is:

```
cleanup_previous_resources   Delete leftover job/PVC/copy-pod
        |
create_benchmark_resources   Create ephemeral PVC + render and apply Job
        |
wait_for_completion          Poll job status until succeeded or failed
        |
capture_benchmark_state      Save job YAML, pod YAML, job logs     (@always)
        |
copy_benchmark_results       Spawn copy pod on same node,           (@always)
                             oc exec cat results to local artifacts
        |
cleanup_benchmark_resources  Delete job + PVC + copy pod             (@always)
```

## Configuration

- Project config: [`orchestration/config.yaml`](./orchestration/config.yaml)

Key sections:

| Section | Purpose |
|---------|---------|
| `rhaiis` | Namespace, accelerator, vLLM images, deploy settings, vLLM args |
| `models` | Model definitions (hf_model_id, per-model vLLM overrides) |
| `workloads` | Benchmark profiles (data shape, rates, max_seconds) |
| `benchmarks.guidellm` | GuideLLM image, backend, timeout, PVC size |
| `tests` | CI test mapping (model_key, workload_key) |

## Main entrypoints

- CLI: [`orchestration/cli.py`](./orchestration/cli.py)
- CI test: [`orchestration/test_rhaiis.py`](./orchestration/test_rhaiis.py)

## Toolbox commands

| Command | Purpose |
|---------|---------|
| [`deploy_kserve_isvc`](./toolbox/deploy_kserve_isvc/) | Render and apply KServe InferenceService + ServingRuntime |
| [`wait_isvc_ready`](./toolbox/wait_isvc_ready/) | Poll InferenceService readiness with health check |
| [`run_guidellm_benchmark`](./toolbox/run_guidellm_benchmark/) | Run GuideLLM benchmark with ephemeral PVC and copy-pod result extraction |
| [`capture_isvc_state`](./toolbox/capture_isvc_state/) | Capture InferenceService YAML, pod logs, events, describe output |
| [`cleanup_isvc`](./toolbox/cleanup_isvc/) | Delete InferenceService, ServingRuntime, wait for deletion |

## Usage

```bash
# Activate the virtualenv
source ~/test_foo/python3_virt/bin/activate

# Dry run
python3 -m projects.rhaiis.orchestration.cli test \
  --model qwen3-0_6b --workload short --namespace kserve-e2e-perf --dry-run

# Full E2E test
python3 -m projects.rhaiis.orchestration.cli test \
  --model qwen3-0_6b \
  --workload short \
  --namespace kserve-e2e-perf \
  --image-pull-secret npalaska-image-pull

# Custom rates
python3 -m projects.rhaiis.orchestration.cli test \
  --model llama-3-1-8b-fp8 \
  --workload balanced \
  --namespace kserve-e2e-perf \
  --image-pull-secret npalaska-image-pull \
  --rates 1,10,50 --max-seconds 60

# Cleanup only
python3 -m projects.rhaiis.orchestration.cli cleanup \
  --deployment-name qwen3-0-6b --namespace kserve-e2e-perf
```

## Result extraction

GuideLLM results are extracted using the copy-pod pattern (same as llm_d):

1. GuideLLM job writes `benchmarks.json` to a PVC mounted at `/results`
2. A copy pod is created on the same node (required for ReadWriteOnce PVC)
3. Results are extracted via `oc exec cat /results/benchmarks.json`
4. Written to local `artifacts/results/benchmarks.json`
5. PVC, job, and copy pod are deleted

Artifacts are stored under `/tmp/forge_<timestamp>/002__run_guidellm_benchmark/artifacts/`.

## Available models

| Key | Model | Notes |
|-----|-------|-------|
| `qwen3-0_6b` | Qwen/Qwen3-0.6B | Small, fast for validation |
| `llama-3-1-8b-fp8` | RedHatAI/Meta-Llama-3.1-8B-Instruct-FP8 | Medium, max-model-len 16384 |
| `llama-3-3-70b-fp8` | RedHatAI/Llama-3.3-70B-Instruct-FP8 | Large, tensor-parallel-size 4 |

## Available workloads

| Key | Data shape | Rates | Max seconds |
|-----|-----------|-------|-------------|
| `balanced` | 1000 in / 1000 out | 1, 50, 100, 200 | 180 |
| `short` | 256 in / 256 out | 1, 50, 100, 200 | 120 |
| `long-prompt` | 8000 in / 1000 out | 1, 25, 50, 100 | 300 |
