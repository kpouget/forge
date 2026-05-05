"""
Config-driven Caliper parse / visualize / KPI / analyze for FORGE orchestration.

Computes a single ``final_status`` from the FORGE test phase outcome plus step results.
"""

from __future__ import annotations

import logging
import traceback
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from projects.caliper.engine.kpi.analyze import run_analyze
from projects.caliper.engine.kpi.generate import run_kpi_generate
from projects.caliper.engine.kpi.import_export import export_kpis_to_index
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


def _resolve_paths(
    ppc: CaliperOrchestrationPostprocessConfig,
    *,
    artifacts_dir: Path,
) -> tuple[Path, Path | None, Path | None]:
    manifest_path = (
        Path(ppc.postprocess_config).expanduser().resolve() if ppc.postprocess_config else None
    )
    cache_path = Path(ppc.parse.cache_dir).expanduser().resolve() if ppc.parse.cache_dir else None
    return artifacts_dir.resolve(), manifest_path, cache_path


def _resolve_visualize_output_dir(
    raw: str | None,
    *,
    orchestration_artifact_dir: Path | None,
) -> Path:
    if raw is None or not str(raw).strip():
        base = orchestration_artifact_dir
        if base is None:
            raise ValueError(
                "caliper.postprocess.visualize.output_dir is unset and ARTIFACT_DIR is not available"
            )
        return (base / "caliper_visualization").resolve()
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p.resolve()
    if orchestration_artifact_dir is None:
        raise ValueError(
            "Relative caliper.postprocess.visualize.output_dir requires ARTIFACT_DIR "
            "(or pass an absolute output_dir)."
        )
    return (orchestration_artifact_dir / p).resolve()


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
    ppc: CaliperOrchestrationPostprocessConfig,
    *,
    tree_root: Path,
    manifest_path: Path | None,
) -> tuple[str, object]:
    mod_str, _manifest = resolve_plugin_module_string(
        base_dir=tree_root,
        postprocess_config=manifest_path,
        cli_plugin=ppc.plugin_module,
    )
    return mod_str, load_plugin(mod_str)


def _resolve_under_workspace(raw: str | None, default_name: str, workspace: Path | None) -> Path:
    name = (raw or default_name).strip()
    p = Path(name)
    if p.is_absolute():
        return p.resolve()
    if workspace is None:
        raise ValueError(f"Relative path {name!r} requires a post-processing workspace directory.")
    return (workspace / p).resolve()


def _resolve_baseline_path(raw: str, *, artifact_tree: Path) -> Path:
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p.resolve()
    return (artifact_tree / p).resolve()


def run_postprocess_from_orchestration_config(
    caliper_cfg: dict[str, Any] | None,
    *,
    artifacts_dir: Path,
    orchestration_artifact_dir: Path | None = None,
    visualize_output_directory: Path | None = None,
    postprocessing_workspace: Path | None = None,
    test_outcome: TestPhaseOutcome | None = None,
) -> dict[str, Any]:
    """
    Run enabled parse / visualize / KPI / analyze steps and compute ``final_status``.

    ``postprocessing_workspace`` is used for KPI JSONL / analyze JSON when paths are relative
    (typically the ``NextArtifactDir('postprocessing')`` directory).
    """
    outcome = test_outcome or TestPhaseOutcome("SUCCESS")
    steps: dict[str, Any] = {}
    parse_failed = False
    visualize_failed = False
    kpi_generate_failed = False
    kpi_export_failed = False
    analyze_failed = False
    has_regression = False
    has_improvement = False

    workspace = postprocessing_workspace or visualize_output_directory or orchestration_artifact_dir

    try:
        root = caliper_cfg or {}
        ppc = CaliperOrchestrationPostprocessConfig.model_validate(root.get("postprocess") or {})
    except ValidationError as e:
        logger.error("Invalid caliper postprocess config: %s", e)
        raise

    test_block = {"phase": outcome.phase, "message": outcome.message}

    if not ppc.enabled:
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

    tree_root, manifest_path, cache_path = _resolve_paths(ppc, artifacts_dir=artifacts_dir)

    def _run_parse() -> dict[str, Any]:
        nonlocal parse_failed
        if not ppc.parse.enabled:
            return {"status": "skipped", "reason": "parse disabled"}
        try:
            mod_str, plugin = _load_plugin(ppc, tree_root=tree_root, manifest_path=manifest_path)
            model = run_parse(
                base_dir=tree_root,
                plugin_module=mod_str,
                plugin=plugin,
                use_cache=not ppc.parse.no_cache,
                cache_path=cache_path,
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
        if not ppc.visualize.enabled:
            return {"status": "skipped", "reason": "visualize disabled"}
        try:
            mod_str, plugin = _load_plugin(ppc, tree_root=tree_root, manifest_path=manifest_path)
            viz_cfg_path = _resolve_visualize_config_path(
                ppc.visualize.visualize_config,
                artifact_tree=tree_root,
            )
            if visualize_output_directory is not None:
                output_dir = visualize_output_directory.expanduser().resolve()
            else:
                output_dir = _resolve_visualize_output_dir(
                    ppc.visualize.output_dir,
                    orchestration_artifact_dir=orchestration_artifact_dir,
                )
            paths = run_visualize(
                base_dir=tree_root,
                plugin_module=mod_str,
                plugin=plugin,
                output_dir=output_dir,
                reports_csv=ppc.visualize.reports,
                report_group=ppc.visualize.report_group,
                visualize_config_path=viz_cfg_path,
                include_pairs=tuple(ppc.visualize.include_labels),
                exclude_pairs=tuple(ppc.visualize.exclude_labels),
                use_cache=not ppc.parse.no_cache,
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

    def _run_kpi() -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]] | None]:
        nonlocal kpi_generate_failed, kpi_export_failed
        empty: dict[str, Any] = {}
        if not ppc.kpi.enabled:
            return {"status": "skipped", "reason": "kpi disabled"}, empty, None
        rows: list[dict[str, Any]] | None = None
        gen_report: dict[str, Any] = {}
        exp_report: dict[str, Any] = {}

        if ppc.kpi.generate.enabled:
            try:
                mod_str, plugin = _load_plugin(
                    ppc, tree_root=tree_root, manifest_path=manifest_path
                )
                out_kpi = _resolve_under_workspace(ppc.kpi.generate.output, "kpis.jsonl", workspace)
                rows = run_kpi_generate(
                    base_dir=tree_root,
                    plugin_module=mod_str,
                    plugin=plugin,
                    output=out_kpi,
                    use_cache=not ppc.parse.no_cache,
                    cache_path=cache_path,
                )
                gen_report = {
                    "status": "ok",
                    "plugin_module": mod_str,
                    "output": str(out_kpi),
                    "record_count": len(rows),
                }
            except Exception as e:  # noqa: BLE001
                kpi_generate_failed = True
                logger.exception("KPI generate failed")
                gen_report = {
                    "status": "failure",
                    "detail": str(e),
                    "traceback": traceback.format_exc(),
                }
                rows = None
        else:
            gen_report = {"status": "skipped", "reason": "kpi.generate disabled"}

        if ppc.kpi.export.enabled:
            if rows is None:
                kpi_export_failed = True
                exp_report = {
                    "status": "failure",
                    "detail": "kpi.export requires successful kpi.generate in the same run.",
                }
            else:
                try:
                    export_kpis_to_index(rows)
                    exp_report = {"status": "ok"}
                except Exception as e:  # noqa: BLE001
                    kpi_export_failed = True
                    logger.exception("KPI export failed")
                    exp_report = {
                        "status": "failure",
                        "detail": str(e),
                        "traceback": traceback.format_exc(),
                    }
        else:
            exp_report = {"status": "skipped", "reason": "kpi.export disabled"}

        return gen_report, exp_report, rows

    def _run_analyze(current_kpi_path: Path | None) -> dict[str, Any]:
        nonlocal has_regression, has_improvement, analyze_failed
        if not ppc.analyze.enabled:
            return {"status": "skipped", "reason": "analyze disabled"}
        if current_kpi_path is None or not current_kpi_path.is_file():
            analyze_failed = True
            return {
                "status": "failure",
                "detail": f"Missing KPI file for analyze: {current_kpi_path}",
            }
        try:
            baseline_path = _resolve_baseline_path(
                ppc.analyze.baseline or "", artifact_tree=tree_root
            )
            out_path = _resolve_under_workspace(ppc.analyze.output, "kpi_analyze.json", workspace)
            findings = run_analyze(
                current_path=current_kpi_path,
                baseline_path=baseline_path,
                output_path=out_path,
            )
            has_regression = any(f.status == "regression" for f in findings)
            has_improvement = any(f.status == "improvement" for f in findings)
            return {
                "status": "ok",
                "output": str(out_path),
                "regression_count": sum(1 for f in findings if f.status == "regression"),
                "improvement_count": sum(1 for f in findings if f.status == "improvement"),
            }
        except Exception as e:  # noqa: BLE001
            analyze_failed = True
            logger.exception("KPI analyze failed")
            return {"status": "failure", "detail": str(e), "traceback": traceback.format_exc()}

    any_step = ppc.parse.enabled or ppc.visualize.enabled or ppc.kpi.enabled or ppc.analyze.enabled
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

    if ppc.parse.enabled:
        steps["parse"] = _run_parse()
    if ppc.visualize.enabled:
        steps["visualize"] = _run_visualize()

    current_kpi_path: Path | None = None
    if ppc.kpi.enabled:
        gen_r, exp_r, rows = _run_kpi()
        steps["kpi_generate"] = gen_r
        steps["kpi_export"] = exp_r
        if rows is not None and ppc.kpi.generate.enabled:
            current_kpi_path = _resolve_under_workspace(
                ppc.kpi.generate.output, "kpis.jsonl", workspace
            )

    if ppc.analyze.enabled:
        steps["analyze"] = _run_analyze(current_kpi_path)

    final = compute_final_postprocess_status(
        test_outcome=outcome,
        parse_failed=parse_failed,
        visualize_failed=visualize_failed,
        kpi_generate_failed=kpi_generate_failed,
        kpi_export_failed=kpi_export_failed,
        analyze_failed=analyze_failed,
        has_regression=has_regression,
        has_improvement=has_improvement,
    )

    return {
        "final_status": final,
        "test_phase": test_block,
        "steps": steps,
    }
