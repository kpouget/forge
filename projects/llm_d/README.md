# llm_d

`llm_d` is the Forge project for validating downstream llm-d on RHOAI.

The current implementation is intentionally narrow:

- target only downstream `LLMInferenceService`
- keep the public interface compatible with current Fournos phase execution
- use checked-in config chunks and manifests instead of a large mutable config surface

Configuration layout:

- project config chunk: [`orchestration/config.d/project.yaml`](./orchestration/config.d/project.yaml)
- config chunks: [`orchestration/config.d`](./orchestration/config.d)
- presets: [`orchestration/presets.d`](./orchestration/presets.d)
- manifests: [`orchestration/manifests`](./orchestration/manifests)

Main entrypoints:

- CI phase wrapper: [`orchestration/ci.py`](./orchestration/ci.py)
- CLI wrapper: [`orchestration/cli.py`](./orchestration/cli.py)
- Shared runtime/config loader: [`runtime/llmd_runtime.py`](./runtime/llmd_runtime.py)
- Toolbox prepare command: [`toolbox/prepare/main.py`](./toolbox/prepare/main.py)
- Toolbox test command: [`toolbox/test/main.py`](./toolbox/test/main.py)
- Toolbox cleanup command: [`toolbox/cleanup/main.py`](./toolbox/cleanup/main.py)
