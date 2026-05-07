"""
Config-driven Caliper parse / visualize for FORGE orchestration.

KPI generation, export, and regression analyze are reserved in config but **not** implemented
here; step entries document ``skipped`` stubs so callers keep a stable ``steps`` shape.

Computes ``final_status`` from the FORGE test phase outcome plus parse/visualize results.
"""

from __future__ import annotations

import logging
import traceback
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from projects.caliper.engine.load_plugin import load_plugin
from projects.caliper.engine.parse import run_parse
from projects.caliper.engine.plugin_config import resolve_plugin_module_string
from projects.caliper.engine.visualize import run_visualize
from projects.caliper.orchestration.postprocess_config import (
    CaliperOrchestrationPostprocessConfig,
)
from projects.caliper.orchestration.postprocess_outcome import (
    TestPhaseOutcome,
    compute_final_postprocess_status,
)

logger = logging.getLogger(__name__)

_STUB_REASON_KPI = (
    "orchestration stub: KPI steps are not wired here (use Caliper CLI or extend orchestration)."
)
_STUB_REASON_ANALYZE = "orchestration stub: regression analyze is not wired here (use Caliper CLI or extend orchestration)."


def _resolve_paths(
    postprocess_config: CaliperOrchestrationPostprocessConfig,
    *,
    artifacts_dir: Path,
) -> tuple[Path, Path | None, Path | None]:
    manifest_path = (
        Path(postprocess_config.postprocess_config).expanduser().resolve()
        if postprocess_config.postprocess_config
        else None
    )
    # Always use default cache behavior - store cache files with each test result
    cache_path = None
    return artifacts_dir.resolve(), manifest_path, cache_path


def _resolve_visualize_output_dir(
    raw: str | None,
) -> Path:
    if raw is None or not str(raw).strip():
        raise ValueError(
            "caliper.postprocess.visualize.output_dir is required when no explicit visualize_output_directory is provided"
        )
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p.resolve()
    raise ValueError("caliper.postprocess.visualize.output_dir must be an absolute path")


def _resolve_visualize_config_path(
    raw: str | None,
    *,
    artifact_tree: Path,
) -> Path | None:
    if raw is None or not str(raw).strip():
        return None
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p.resolve()
    return (artifact_tree / p).resolve()


def _load_plugin(
    postprocess_config: CaliperOrchestrationPostprocessConfig,
    *,
    tree_root: Path,
    manifest_path: Path | None,
) -> tuple[str, object]:
    mod_str, _manifest = resolve_plugin_module_string(
        base_dir=tree_root,
        postprocess_config=manifest_path,
        cli_plugin=postprocess_config.plugin_module,
    )
    return mod_str, load_plugin(mod_str)


def _stub_kpi_generate(postprocess_config: CaliperOrchestrationPostprocessConfig) -> dict[str, Any]:
    if not postprocess_config.kpi.enabled:
        return {"status": "skipped", "reason": "kpi disabled"}
    if not postprocess_config.kpi.generate.enabled:
        return {"status": "skipped", "reason": "kpi.generate disabled"}
    return {"status": "skipped", "reason": _STUB_REASON_KPI}


def _stub_kpi_export(postprocess_config: CaliperOrchestrationPostprocessConfig) -> dict[str, Any]:
    if not postprocess_config.kpi.enabled:
        return {"status": "skipped", "reason": "kpi disabled"}
    if not postprocess_config.kpi.export.enabled:
        return {"status": "skipped", "reason": "kpi.export disabled"}
    return {"status": "skipped", "reason": _STUB_REASON_KPI}


def _stub_analyze(postprocess_config: CaliperOrchestrationPostprocessConfig) -> dict[str, Any]:
    if not postprocess_config.analyze.enabled:
        return {"status": "skipped", "reason": "analyze disabled"}
    return {"status": "skipped", "reason": _STUB_REASON_ANALYZE}


def run_postprocess_from_orchestration_config(
    postprocess_config_raw: dict[str, Any] | None,
    *,
    artifacts_dir: Path,
    visualize_output_dir: Path | None = None,
    test_outcome: TestPhaseOutcome | None = None,
) -> dict[str, Any]:
    """
    Run enabled parse / visualize steps and compute ``final_status``.

    KPI and analyze sections only emit stub ``steps`` entries (never failures).

    Parse/visualize use ``artifacts_dir`` and ``visualize_output_dir``.
    """
    outcome = test_outcome or TestPhaseOutcome("NOT_AVAILABLE")
    steps: dict[str, Any] = {}
    parse_failed = False
    visualize_failed = False

    try:
        postprocess_config = CaliperOrchestrationPostprocessConfig.model_validate(
            postprocess_config_raw or {}
        )
    except ValidationError as e:
        logger.error("Invalid caliper postprocess config: %s", e)
        raise

    test_block = {"phase": outcome.phase, "message": outcome.message}

    if not postprocess_config.enabled:
        logger.info("caliper.postprocess.enabled is false — skipping post-processing steps")
        final = compute_final_postprocess_status(
            test_outcome=outcome,
            parse_failed=False,
            visualize_failed=False,
            kpi_generate_failed=False,
            kpi_export_failed=False,
            analyze_failed=False,
            has_regression=False,
            has_improvement=False,
        )
        return {
            "final_status": final,
            "test_phase": test_block,
            "steps": {},
        }

    tree_root, manifest_path, cache_path = _resolve_paths(
        postprocess_config, artifacts_dir=artifacts_dir
    )

    def _run_parse() -> dict[str, Any]:
        nonlocal parse_failed
        if not postprocess_config.parse.enabled:
            return {"status": "skipped", "reason": "parse disabled"}
        try:
            mod_str, plugin = _load_plugin(
                postprocess_config, tree_root=tree_root, manifest_path=manifest_path
            )
            model = run_parse(
                base_dir=tree_root,
                plugin_module=mod_str,
                plugin=plugin,
                use_cache=not postprocess_config.parse.no_cache,
            )
            return {
                "status": "ok",
                "plugin_module": mod_str,
                "record_count": len(model.unified_result_records),
                "parse_cache_ref": model.parse_cache_ref,
            }
        except Exception as e:  # noqa: BLE001
            parse_failed = True
            logger.exception("Caliper parse failed")
            return {"status": "failure", "detail": str(e), "traceback": traceback.format_exc()}

    def _run_visualize() -> dict[str, Any]:
        nonlocal visualize_failed
        if not postprocess_config.visualize.enabled:
            return {"status": "skipped", "reason": "visualize disabled"}
        try:
            mod_str, plugin = _load_plugin(
                postprocess_config, tree_root=tree_root, manifest_path=manifest_path
            )
            viz_cfg_path = _resolve_visualize_config_path(
                postprocess_config.visualize.visualize_config,
                artifact_tree=tree_root,
            )
            if visualize_output_dir is not None:
                output_dir = visualize_output_dir.expanduser().resolve()
            else:
                output_dir = _resolve_visualize_output_dir(
                    postprocess_config.visualize.output_dir,
                )
            paths = run_visualize(
                base_dir=tree_root,
                plugin_module=mod_str,
                plugin=plugin,
                output_dir=output_dir,
                reports_csv=postprocess_config.visualize.reports,
                report_group=postprocess_config.visualize.report_group,
                visualize_config_path=viz_cfg_path,
                include_pairs=tuple(postprocess_config.visualize.include_labels),
                exclude_pairs=tuple(postprocess_config.visualize.exclude_labels),
                use_cache=not postprocess_config.parse.no_cache,
                cache_path=cache_path,
            )
            # Convert paths to relative paths from output_dir
            relative_paths = []
            for path in paths:
                try:
                    path_obj = Path(path)
                    relative_path = path_obj.relative_to(output_dir)
                    relative_paths.append(str(relative_path))
                except ValueError:
                    # If path is not under output_dir, keep as-is
                    relative_paths.append(str(path))

            return {
                "status": "ok",
                "plugin_module": mod_str,
                "output_dir": str(output_dir),
                "paths": relative_paths,
            }
        except Exception as e:  # noqa: BLE001
            visualize_failed = True
            logger.exception("Caliper visualize failed")
            return {"status": "failure", "detail": str(e), "traceback": traceback.format_exc()}

    any_step = (
        postprocess_config.parse.enabled
        or postprocess_config.visualize.enabled
        or postprocess_config.kpi.enabled
        or postprocess_config.analyze.enabled
    )
    if not any_step:
        logger.info("caliper.postprocess: no parse/visualize/kpi/analyze steps enabled")
        final = compute_final_postprocess_status(
            test_outcome=outcome,
            parse_failed=False,
            visualize_failed=False,
            kpi_generate_failed=False,
            kpi_export_failed=False,
            analyze_failed=False,
            has_regression=False,
            has_improvement=False,
        )
        return {"final_status": final, "test_phase": test_block, "steps": {}}

    if postprocess_config.parse.enabled:
        steps["parse"] = _run_parse()
    if postprocess_config.visualize.enabled:
        steps["visualize"] = _run_visualize()

    if postprocess_config.kpi.enabled:
        steps["kpi_generate"] = _stub_kpi_generate(postprocess_config)
        steps["kpi_export"] = _stub_kpi_export(postprocess_config)

    if postprocess_config.analyze.enabled:
        steps["analyze"] = _stub_analyze(postprocess_config)

    final = compute_final_postprocess_status(
        test_outcome=outcome,
        parse_failed=parse_failed,
        visualize_failed=visualize_failed,
        kpi_generate_failed=False,
        kpi_export_failed=False,
        analyze_failed=False,
        has_regression=False,
        has_improvement=False,
    )

    return {
        "final_status": final,
        "test_phase": test_block,
        "steps": steps,
    }
