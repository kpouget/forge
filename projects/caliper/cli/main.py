"""Caliper CLI."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, NoReturn

import click
import yaml

from projects.caliper.engine.ai_eval import run_ai_eval_export
from projects.caliper.engine.file_export.artifacts_export_run import run_artifacts_export
from projects.caliper.engine.file_export.mlflow_config import load_mlflow_config_yaml
from projects.caliper.engine.html_index import generate_html_index
from projects.caliper.engine.kpi.analyze import run_analyze
from projects.caliper.engine.kpi.generate import run_kpi_generate
from projects.caliper.engine.kpi.import_export import (
    export_kpis_to_index,
    import_kpis_snapshot,
    load_kpis_jsonl,
)
from projects.caliper.engine.load_plugin import load_plugin
from projects.caliper.engine.parse import run_parse
from projects.caliper.engine.plugin_config import resolve_plugin_module_string
from projects.caliper.engine.visualize import run_visualize

_ARTIFACTS_DIR_HELP = (
    "Root directory of the test artifact tree (directories containing "
    "__test_labels__.yaml). Optional manifest files (e.g. caliper.yaml) are searched here "
    "unless --postprocess-config is set."
)
_PLUGIN_MODULE_HELP = (
    "Python import path of the Caliper plugin module (must expose get_plugin()). "
    "Names the plugin implementation; overrides plugin_module in the manifest when both "
    "are set."
)
_POSTPROCESS_CONFIG_HELP = (
    "Path to the post-processing manifest (YAML). If omitted, conventional filenames "
    "are searched under the artifact tree (--artifacts-dir)."
)


def _root_obj(ctx: click.Context) -> dict[str, Any]:
    while ctx.parent is not None:
        ctx = ctx.parent
    return ctx.obj


def _exit_with_help(ctx: click.Context, message: str, code: int = 1) -> NoReturn:
    """Print error line and this command's --help text."""
    click.echo(f"Error: {message}\n", err=True)
    click.echo(ctx.get_help(), err=True)
    ctx.exit(code)


def _require_artifacts_dir(ctx: click.Context) -> Path:
    obj = _root_obj(ctx)
    bd = obj.get("base_dir")
    if bd is None:
        _exit_with_help(
            ctx,
            "This command requires the test artifact tree root: "
            "`--artifacts-dir DIR` "
            "(before or after the subcommand).",
            code=1,
        )
    return bd  # type: ignore[return-value]


def _apply_workspace_cli_overrides(
    ctx: click.Context,
    *,
    artifacts_dir: Path | None,
    postprocess_config: Path | None,
    plugin_module_override: str | None,
) -> None:
    """Merge subcommand-level workspace flags into the root context (group options win if unset)."""
    obj = _root_obj(ctx)
    if artifacts_dir is not None:
        obj["base_dir"] = artifacts_dir
    if postprocess_config is not None:
        obj["postprocess_config"] = postprocess_config
    if plugin_module_override is not None:
        obj["plugin_cli"] = plugin_module_override


def _workspace_cli_options(cmd: Any) -> Any:
    """Repeat global workspace options so they may appear after the subcommand."""
    opts = (
        click.option(
            "--artifacts-dir",
            "artifacts_dir",
            type=click.Path(path_type=Path, exists=True),
            default=None,
            help=(
                "Test artifact tree root (same meaning as before COMMAND). "
                "Repeat here if you prefer flags after the subcommand."
            ),
        ),
        click.option(
            "--postprocess-config",
            type=click.Path(path_type=Path, dir_okay=False, exists=True),
            default=None,
            help=_POSTPROCESS_CONFIG_HELP + " Overrides the global option when set here.",
        ),
        click.option(
            "--plugin",
            "plugin_module_override",
            metavar="MODULE",
            default=None,
            help="Plugin import path; same as global --plugin.",
        ),
    )
    for opt in reversed(opts):
        cmd = opt(cmd)
    return cmd


def _plugin_tuple(ctx: click.Context) -> tuple[str, Any]:
    base_dir = _require_artifacts_dir(ctx)
    obj = _root_obj(ctx)
    pc: Path | None = obj["postprocess_config"]
    cli_p: str | None = obj["plugin_cli"]
    try:
        mod, _manifest_path = resolve_plugin_module_string(
            base_dir=base_dir,
            postprocess_config=pc,
            cli_plugin=cli_p,
        )
    except (ValueError, FileNotFoundError) as e:
        _exit_with_help(ctx, str(e), code=1)
    try:
        plugin = load_plugin(mod)
    except RuntimeError as e:
        _exit_with_help(ctx, str(e), code=2)
    return mod, plugin


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--artifacts-dir",
    "artifacts_dir",
    type=click.Path(path_type=Path, exists=True),
    default=None,
    help=_ARTIFACTS_DIR_HELP,
)
@click.option(
    "--postprocess-config",
    type=click.Path(path_type=Path, dir_okay=False, exists=True),
    default=None,
    help=_POSTPROCESS_CONFIG_HELP,
)
@click.option(
    "--plugin",
    "plugin_module",
    metavar="MODULE",
    default=None,
    help=_PLUGIN_MODULE_HELP,
)
@click.pass_context
def main(
    ctx: click.Context,
    artifacts_dir: Path | None,
    postprocess_config: Path | None,
    plugin_module: str | None,
) -> None:
    """Caliper — artifact post-processing."""
    ctx.ensure_object(dict)
    ctx.obj["base_dir"] = artifacts_dir
    ctx.obj["postprocess_config"] = postprocess_config
    ctx.obj["plugin_cli"] = plugin_module


@main.command("parse")
@_workspace_cli_options
@click.option("--no-cache", is_flag=True, help="Force full parse.")
@click.option(
    "--cache-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Override cache file path.",
)
@click.option(
    "--show-matrix/--no-show-matrix",
    default=True,
    help="Display parameter matrix summary after parsing (default: enabled).",
)
@click.pass_context
def parse_cmd(
    ctx: click.Context,
    no_cache: bool,
    cache_dir: Path | None,
    show_matrix: bool,
    artifacts_dir: Path | None,
    postprocess_config: Path | None,
    plugin_module_override: str | None,
) -> None:
    _apply_workspace_cli_overrides(
        ctx,
        artifacts_dir=artifacts_dir,
        postprocess_config=postprocess_config,
        plugin_module_override=plugin_module_override,
    )
    mod, plugin = _plugin_tuple(ctx)
    artifact_root: Path = _root_obj(ctx)["base_dir"]
    try:
        model = run_parse(
            base_dir=artifact_root,
            plugin_module=mod,
            plugin=plugin,
            use_cache=not no_cache,
            show_parameter_matrix=show_matrix,
        )
    except Exception as e:  # noqa: BLE001
        click.echo(f"parse failed: {e}", err=True)
        sys.exit(2)
    click.echo(
        f"Parsed {len(model.unified_result_records)} record(s); cache={model.parse_cache_ref}"
    )


@main.command("visualize")
@_workspace_cli_options
@click.option("--reports", default=None, help="Comma-separated report ids.")
@click.option("--report-group", default=None)
@click.option("--visualize-config", type=click.Path(path_type=Path), default=None)
@click.option("--include-label", multiple=True)
@click.option("--exclude-label", multiple=True)
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path),
    required=True,
)
@click.option(
    "--generate-index/--no-index",
    default=True,
    help="Generate HTML index file listing all created reports.",
)
@click.pass_context
def visualize_cmd(
    ctx: click.Context,
    reports: str | None,
    report_group: str | None,
    visualize_config: Path | None,
    include_label: tuple[str, ...],
    exclude_label: tuple[str, ...],
    output_dir: Path,
    generate_index: bool,
    artifacts_dir: Path | None,
    postprocess_config: Path | None,
    plugin_module_override: str | None,
) -> None:
    _apply_workspace_cli_overrides(
        ctx,
        artifacts_dir=artifacts_dir,
        postprocess_config=postprocess_config,
        plugin_module_override=plugin_module_override,
    )
    mod, plugin = _plugin_tuple(ctx)
    artifact_root: Path = _root_obj(ctx)["base_dir"]
    try:
        paths = run_visualize(
            base_dir=artifact_root,
            plugin_module=mod,
            plugin=plugin,
            output_dir=output_dir,
            reports_csv=reports,
            report_group=report_group,
            visualize_config_path=visualize_config,
            include_pairs=include_label,
            exclude_pairs=exclude_label,
            use_cache=True,
            cache_path=None,
        )
    except Exception as e:  # noqa: BLE001
        click.echo(f"visualize failed: {e}", err=True)
        sys.exit(2)

    # Generate HTML index if requested
    if generate_index:
        try:
            index_path = generate_html_index(
                output_dir=output_dir, title="Caliper Reports Index", include_subdirs=True
            )
            click.echo("Wrote: " + ", ".join(paths))
            click.echo(f"Generated index: {index_path}")
        except Exception as e:  # noqa: BLE001
            click.echo("Wrote: " + ", ".join(paths))
            click.echo(f"Warning: Failed to generate HTML index: {e}", err=True)
    else:
        click.echo("Wrote: " + ", ".join(paths))


@main.command("index")
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path),
    required=True,
    help="Directory containing HTML files to index.",
)
@click.option("--title", default="Caliper Reports Index", help="Title for the index page.")
@click.option(
    "--include-subdirs/--no-subdirs", default=True, help="Include HTML files from subdirectories."
)
def index_cmd(
    output_dir: Path,
    title: str,
    include_subdirs: bool,
) -> None:
    """Generate an HTML index of all HTML files in the output directory."""
    try:
        index_path = generate_html_index(
            output_dir=output_dir, title=title, include_subdirs=include_subdirs
        )
        click.echo(f"Generated HTML index: {index_path}")
    except Exception as e:  # noqa: BLE001
        click.echo(f"Failed to generate HTML index: {e}", err=True)
        sys.exit(2)


@main.group("kpi")
@click.pass_context
def kpi_group(ctx: click.Context) -> None:
    """KPI generate/import/export/analyze."""


@kpi_group.command("generate")
@_workspace_cli_options
@click.option("--output", type=click.Path(path_type=Path), required=True)
@click.pass_context
def kpi_generate(
    ctx: click.Context,
    output: Path,
    artifacts_dir: Path | None,
    postprocess_config: Path | None,
    plugin_module_override: str | None,
) -> None:
    _apply_workspace_cli_overrides(
        ctx,
        artifacts_dir=artifacts_dir,
        postprocess_config=postprocess_config,
        plugin_module_override=plugin_module_override,
    )
    mod, plugin = _plugin_tuple(ctx)
    artifact_root: Path = _root_obj(ctx)["base_dir"]
    try:
        run_kpi_generate(
            base_dir=artifact_root,
            plugin_module=mod,
            plugin=plugin,
            output=output,
            use_cache=True,
            cache_path=None,
        )
    except Exception as e:  # noqa: BLE001
        click.echo(f"kpi generate failed: {e}", err=True)
        sys.exit(2)
    click.echo(f"Wrote KPIs to {output}")


@kpi_group.command("import")
@click.option("--snapshot", type=click.Path(path_type=Path), required=True)
@click.pass_context
def kpi_import(ctx: click.Context, snapshot: Path) -> None:
    try:
        import_kpis_snapshot(snapshot_path=snapshot)
    except Exception as e:  # noqa: BLE001
        click.echo(f"kpi import failed: {e}", err=True)
        sys.exit(3)
    click.echo(f"Wrote snapshot {snapshot}")


@kpi_group.command("export")
@click.option("--input", "input_path", type=click.Path(path_type=Path), required=True)
@click.option("--dry-run", is_flag=True)
@click.pass_context
def kpi_export(ctx: click.Context, input_path: Path, dry_run: bool) -> None:
    try:
        rows = load_kpis_jsonl(input_path)
        if dry_run:
            click.echo(f"Would export {len(rows)} records")
            return
        export_kpis_to_index(rows)
    except Exception as e:  # noqa: BLE001
        click.echo(f"kpi export failed: {e}", err=True)
        sys.exit(3)
    click.echo("Export complete")


@kpi_group.command("analyze")
@click.option("--current", type=click.Path(path_type=Path), required=True)
@click.option("--baseline", type=click.Path(path_type=Path), required=True)
@click.option("--output", type=click.Path(path_type=Path), required=True)
@click.pass_context
def kpi_analyze(
    ctx: click.Context,
    current: Path,
    baseline: Path,
    output: Path,
) -> None:
    try:
        run_analyze(current_path=current, baseline_path=baseline, output_path=output)
    except Exception as e:  # noqa: BLE001
        click.echo(f"kpi analyze failed: {e}", err=True)
        sys.exit(3)
    click.echo(f"Wrote {output}")


@main.group("artifacts")
@click.pass_context
def artifacts_group(ctx: click.Context) -> None:
    """File artifact export and import."""


@artifacts_group.command("export")
@click.option(
    "--from",
    "from_path",
    type=click.Path(path_type=Path, exists=True),
    required=True,
    help="File or directory to upload (artifact path).",
)
@click.option("--backend", multiple=True, type=str, help="Repeat: mlflow.")
@click.option(
    "--mlflow-tracking-uri",
    "--mlflow-endpoint",
    "mlflow_tracking_uri",
    default=None,
    envvar="MLFLOW_TRACKING_URI",
    help="MLflow tracking server URI (required for mlflow backend unless MLFLOW_TRACKING_URI is set).",
)
@click.option("--mlflow-experiment", default=None, envvar="MLFLOW_EXPERIMENT_NAME")
@click.option("--mlflow-run-id", default=None, envvar="MLFLOW_RUN_ID")
@click.option(
    "--mlflow-run-name",
    default=None,
    envvar="CALIPER_MLFLOW_RUN_NAME",
    help=(
        "Display name for a new MLflow run (ignored when --mlflow-run-id is set; "
        "otherwise MLflow assigns a random name)."
    ),
)
@click.option(
    "--mlflow-insecure-tls",
    is_flag=True,
    help="Do not verify TLS for the MLflow tracking server (self-signed / private CA). "
    "Equivalent to MLFLOW_TRACKING_INSECURE_TLS=true.",
)
@click.option(
    "--mlflow-secrets",
    "mlflow_secrets_path",
    type=click.Path(path_type=Path, exists=True),
    default=None,
    help=(
        "YAML with credentials only: tracking_uri, token or username/password, TLS options. "
        "Keep separate from --mlflow-config. Values apply only for this process."
    ),
)
@click.option(
    "--mlflow-config",
    "mlflow_config_path",
    type=click.Path(path_type=Path, exists=True),
    default=None,
    help=(
        "YAML with non-secret MLflow settings: tracking_uri (optional), experiment, "
        "run_name, run_id. Separate from --mlflow-secrets; secrets file wins on overlapping keys."
    ),
)
@click.option("--dry-run", is_flag=True)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Print detailed progress and configuration on stderr (no secrets).",
)
@click.option(
    "--status-yaml",
    "status_yaml_path",
    type=click.Path(path_type=Path),
    default=None,
    help=(
        "Write a YAML summary of export outcomes, including MLflow run_url and experiment_url "
        "when the mlflow backend succeeds."
    ),
)
@click.option(
    "--upload-workers",
    type=click.IntRange(min=1, max=64),
    default=10,
    show_default=True,
    help="Parallel upload threads (MLflow).",
)
@click.pass_context
def artifacts_export(
    ctx: click.Context,
    from_path: Path,
    backend: tuple[str, ...],
    mlflow_tracking_uri: str | None,
    mlflow_experiment: str | None,
    mlflow_run_id: str | None,
    mlflow_run_name: str | None,
    mlflow_insecure_tls: bool,
    mlflow_secrets_path: Path | None,
    mlflow_config_path: Path | None,
    dry_run: bool,
    verbose: bool,
    status_yaml_path: Path | None,
    upload_workers: int,
) -> None:
    """Upload file artifacts to MLflow."""
    backends = [b.strip().lower() for b in backend if b.strip()]
    if not backends:
        _exit_with_help(
            ctx,
            "Specify at least one --backend: mlflow "
            "(e.g. --from ./out --backend mlflow --mlflow-endpoint http://...).",
            code=1,
        )
    mlflow_config_data: dict | None = None
    if mlflow_config_path is not None:
        try:
            mlflow_config_data = load_mlflow_config_yaml(mlflow_config_path)
        except (OSError, ValueError, TypeError, yaml.YAMLError) as e:
            click.echo(f"Invalid MLflow settings file ({mlflow_config_path}): {e}", err=True)
            sys.exit(1)

    code = run_artifacts_export(
        from_path=from_path,
        backend=backends,
        mlflow_tracking_uri=mlflow_tracking_uri,
        mlflow_experiment=mlflow_experiment,
        mlflow_run_id=mlflow_run_id,
        mlflow_run_name=mlflow_run_name,
        mlflow_insecure_tls=mlflow_insecure_tls,
        mlflow_secrets_path=mlflow_secrets_path,
        mlflow_config_data=mlflow_config_data,
        dry_run=dry_run,
        verbose=verbose,
        status_yaml_path=status_yaml_path,
        upload_workers=upload_workers,
        click_context=ctx,
    )
    if code != 0:
        sys.exit(code)


@main.command("ai-eval-export")
@_workspace_cli_options
@click.option("--output", type=click.Path(path_type=Path), required=True)
@click.pass_context
def ai_eval_export(
    ctx: click.Context,
    output: Path,
    artifacts_dir: Path | None,
    postprocess_config: Path | None,
    plugin_module_override: str | None,
) -> None:
    _apply_workspace_cli_overrides(
        ctx,
        artifacts_dir=artifacts_dir,
        postprocess_config=postprocess_config,
        plugin_module_override=plugin_module_override,
    )
    mod, plugin = _plugin_tuple(ctx)
    artifact_root: Path = _root_obj(ctx)["base_dir"]
    try:
        run_ai_eval_export(
            base_dir=artifact_root,
            plugin_module=mod,
            plugin=plugin,
            output=output,
            use_cache=True,
        )
    except Exception as e:  # noqa: BLE001
        click.echo(f"ai-eval-export failed: {e}", err=True)
        sys.exit(2)
    click.echo(f"Wrote {output}")


@artifacts_group.command("import")
@click.option("--from-mlflow", "mlflow_run_id", help="MLflow run ID to download artifacts from.")
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path),
    required=True,
    help="Local directory to download artifacts to.",
)
@click.option(
    "--mlflow-tracking-uri",
    envvar="MLFLOW_TRACKING_URI",
    help="MLflow tracking server URI (can be set via MLFLOW_TRACKING_URI).",
)
@click.option(
    "--artifact-path",
    default="",
    help="Specific artifact path to download (default: download all artifacts).",
)
@click.pass_context
def import_command(
    ctx: click.Context,
    mlflow_run_id: str | None,
    output_dir: Path,
    mlflow_tracking_uri: str | None,
    artifact_path: str,
) -> None:
    """Download artifact files from MLflow."""
    if mlflow_run_id:
        if not mlflow_tracking_uri:
            click.echo(
                "Error: MLflow tracking URI required. Set --mlflow-tracking-uri or MLFLOW_TRACKING_URI.",
                err=True,
            )
            sys.exit(1)

        try:
            import mlflow
            from mlflow.tracking import MlflowClient

            # Set tracking URI
            mlflow.set_tracking_uri(mlflow_tracking_uri)
            client = MlflowClient()

            # Download artifacts
            output_dir.mkdir(parents=True, exist_ok=True)

            # Download artifacts to the output directory
            downloaded_path = client.download_artifacts(
                run_id=mlflow_run_id, path=artifact_path, dst_path=str(output_dir)
            )

            # Count downloaded files
            if Path(downloaded_path).is_file():
                downloaded_files = [Path(downloaded_path)]
            else:
                downloaded_files = list(Path(downloaded_path).rglob("*"))
                downloaded_files = [f for f in downloaded_files if f.is_file()]

            click.echo(f"Downloaded {len(downloaded_files)} files to {output_dir}")
            if downloaded_files:
                click.echo("Downloaded files:")
                for file in downloaded_files[:10]:  # Show first 10
                    click.echo(f"  {file.relative_to(output_dir)}")
                if len(downloaded_files) > 10:
                    click.echo(f"  ... and {len(downloaded_files) - 10} more")

        except Exception as e:  # noqa: BLE001
            click.echo(f"artifacts import failed: {e}", err=True)
            sys.exit(2)
    else:
        click.echo("Error: Specify source backend: --from-mlflow RUN_ID", err=True)
        sys.exit(1)


def run_cli() -> None:
    """Invoke CLI; on missing required options, print subcommand help."""
    try:
        # standalone_mode=False returns exit codes instead of calling sys.exit;
        # propagate them so failures are non-zero (e.g. ctx.exit(1) from _exit_with_help).
        rv = main.main(standalone_mode=False, prog_name="caliper")
        if isinstance(rv, int) and rv != 0:
            sys.exit(rv)
    except click.ClickException as exc:
        # Handle click exceptions including NoArgsIsHelpError and MissingParameter
        if hasattr(exc, "ctx") and exc.ctx:
            click.echo(exc.ctx.get_help(), err=True)
        else:
            exc.show(sys.stderr)
        sys.exit(2)
    except SystemExit:
        raise


if __name__ == "__main__":
    run_cli()
