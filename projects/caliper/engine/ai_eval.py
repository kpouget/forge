"""AI agent evaluation JSON (FR-011)."""

from __future__ import annotations

from pathlib import Path

from projects.caliper.engine.parse import run_parse
from projects.caliper.engine.validation import load_schema, schema_path, validate_instance


def run_ai_eval_export(
    *,
    base_dir: Path,
    plugin_module: str,
    plugin: object,
    output: Path,
    use_cache: bool,
) -> dict[str, object]:
    model = run_parse(
        base_dir=base_dir,
        plugin_module=plugin_module,
        plugin=plugin,
        use_cache=use_cache,
    )
    build = plugin.build_ai_eval_payload
    payload = build(model)
    schema = load_schema(schema_path("ai_eval_payload.schema.json"))
    validate_instance(payload, schema, "AI eval payload")
    import json  # noqa: PLC0415

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload
