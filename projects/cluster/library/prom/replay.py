from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import yaml

from projects.cluster.library.prom import metrics as prom_metrics
from projects.core.library import config

logger = logging.getLogger(__name__)

PROMETHEUS_URL = "http://localhost:9090"
KUBE_BURNER_FIELDS = {"query", "metricName", "instant", "captureStart"}


def find_prom_dbs(test_dir: Path) -> list[Path]:
    """Find open_metrics.gz files in test artifacts, excluding UWM ones."""
    return [p for p in test_dir.rglob("open_metrics.gz") if "uwm" not in str(p)]


def _find_existing_capture_dirs(artifacts_dir: Path) -> dict[str, Path]:
    """Map group names to their existing capture directories.

    Scans for directories matching ``*__capture_prometheus_metrics_*``
    and extracts the group name from the suffix.
    """
    result: dict[str, Path] = {}
    for d in artifacts_dir.rglob("*__capture_prometheus_metrics_*"):
        if not d.is_dir():
            continue
        parts = d.name.split("__capture_prometheus_metrics_", 1)
        if len(parts) == 2:
            result[parts[1]] = d
    return result


def _check_group_cache(capture_dir: Path, metadata: list[prom_metrics.MetricMetadata]) -> bool:
    """Return True if all expected metric JSON files exist in *capture_dir*."""
    for meta in metadata:
        if not (capture_dir / f"{meta.metric_name}.json").exists():
            return False
    return True


def _read_saved_variables(capture_dir: Path) -> dict[str, str]:
    """Read resolved Go-template variables saved during original capture."""
    variables_path = capture_dir / "src" / "variables.yaml"
    if not variables_path.exists():
        return {}
    with variables_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return dict(data) if isinstance(data, dict) else {}


def _decompress_openmetrics(gz_path: Path, output_path: Path) -> None:
    """Decompress an open_metrics.gz file."""
    with gzip.open(gz_path, "rb") as f_in, output_path.open("wb") as f_out:
        shutil.copyfileobj(f_in, f_out)


def _import_to_tsdb(openmetrics_path: Path, tsdb_dir: Path) -> None:
    """Import an OpenMetrics text file into a Prometheus TSDB."""
    result = subprocess.run(
        [
            "promtool",
            "tsdb",
            "create-blocks-from",
            "openmetrics",
            str(openmetrics_path),
            str(tsdb_dir),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"promtool tsdb create-blocks-from failed: {result.stderr}")
    logger.info("Imported OpenMetrics data into TSDB at %s", tsdb_dir)


def _start_prometheus(tsdb_path: Path, work_dir: Path) -> subprocess.Popen:
    """Start a local Prometheus instance and wait for it to become ready."""
    config_path = work_dir / "prometheus.yml"
    config_path.write_text("global: {}\n", encoding="utf-8")

    prom_proc = subprocess.Popen(
        [
            "prometheus",
            f"--storage.tsdb.path={tsdb_path}",
            f"--config.file={config_path}",
            "--storage.tsdb.retention.time=365d",
            "--web.listen-address=:9090",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    max_wait = 30
    for _ in range(max_wait):
        if prom_proc.poll() is not None:
            stderr = prom_proc.stderr.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Prometheus exited unexpectedly (code {prom_proc.returncode}): {stderr}"
            )
        try:
            urllib.request.urlopen(f"{PROMETHEUS_URL}/-/ready", timeout=2)
            logger.info("Local Prometheus is ready")
            return prom_proc
        except Exception:
            time.sleep(1)

    prom_proc.terminate()
    prom_proc.wait()
    raise RuntimeError(f"Prometheus did not become ready within {max_wait}s")


def _stop_prometheus(prom_proc: subprocess.Popen) -> None:
    """Terminate a local Prometheus instance."""
    prom_proc.terminate()
    try:
        prom_proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        prom_proc.kill()
        prom_proc.wait()
    logger.info("Local Prometheus stopped")


def _get_time_bounds() -> tuple[int, int]:
    """Query the local Prometheus TSDB status to get the data time range.

    Returns (start_epoch_seconds, end_epoch_seconds).
    """
    resp = urllib.request.urlopen(f"{PROMETHEUS_URL}/api/v1/status/tsdb", timeout=10)
    data = json.loads(resp.read())

    tsdb_status = data.get("data", {})
    min_time_ms = tsdb_status.get("minTime", 0)
    max_time_ms = tsdb_status.get("maxTime", 0)

    if not min_time_ms or not max_time_ms:
        raise RuntimeError(f"Could not determine TSDB time bounds: {tsdb_status}")

    return int(min_time_ms / 1000), int(max_time_ms / 1000)


def _clean_profiles_for_kube_burner(
    profiles: list[Path],
    work_dir: Path,
    variables: dict[str, str],
) -> list[Path]:
    """Strip metric profiles to kube-burner fields and extract param_defaults.

    Mirrors the cleaning logic in capture_prometheus_metrics/main.py.
    """
    cleaned_paths: list[Path] = []
    for p in profiles:
        with p.open("r", encoding="utf-8") as f:
            entries = yaml.safe_load(f)

        for entry in entries:
            if isinstance(entry, dict) and "param_defaults" in entry:
                for k, v in entry["param_defaults"].items():
                    variables.setdefault(k, str(v))

        cleaned = [
            {k: v for k, v in entry.items() if k in KUBE_BURNER_FIELDS}
            for entry in entries
            if isinstance(entry, dict) and "metricName" in entry
        ]
        cleaned_path = work_dir / p.name
        with cleaned_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(cleaned, f, default_flow_style=False, sort_keys=False)
        cleaned_paths.append(cleaned_path)

    return cleaned_paths


def _build_kube_burner_config(
    profiles: list[Path],
    output_dir: Path,
    step_seconds: int = 15,
) -> Path:
    """Write a kube-burner endpoint config YAML for a local Prometheus."""
    config_data = [
        {
            "endpoint": PROMETHEUS_URL,
            "step": f"{step_seconds}s",
            "metrics": [str(p) for p in profiles],
            "indexer": {
                "type": "local",
                "metricsDirectory": str(output_dir),
            },
        }
    ]
    config_path = profiles[0].parent / "kube-burner-config.yaml"
    with config_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(config_data, f, default_flow_style=False, sort_keys=False)
    return config_path


def _run_kube_burner(
    config_path: Path,
    start_epoch: int,
    end_epoch: int,
    variables: dict[str, str],
) -> None:
    """Run kube-burner index locally."""
    env = {**os.environ, **variables}
    result = subprocess.run(
        [
            "kube-burner",
            "index",
            "--skip-log-file",
            "-e",
            str(config_path),
            "--start",
            str(start_epoch),
            "--end",
            str(end_epoch),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"kube-burner index failed (code {result.returncode}): {result.stderr}")
    logger.info("kube-burner index completed")


def replay_metrics_from_config(
    artifacts_dir: Path,
    *,
    force_refresh: bool = False,
) -> list[str]:
    """Replay missing Prometheus metrics from captured TSDB data.

    Reads the project config (``prom.capture.metrics.*``), finds captured
    ``open_metrics.gz`` archives in *artifacts_dir*, and for each metric
    group with missing JSON files, starts a local Prometheus and runs
    kube-burner to fetch them.

    When *force_refresh* is True, all groups are replayed regardless of
    whether their metric JSON files already exist.

    Returns a list of group names that had errors (empty = success).
    """
    for binary in ("prometheus", "promtool", "kube-burner"):
        if not shutil.which(binary):
            logger.warning("%s not found in PATH, skipping Prometheus replay", binary)
            return []

    if not config.project.get_config("prom.capture.metrics.enabled", False):
        logger.info("Prometheus metrics capture not enabled, skipping replay.")
        return []

    prom_dbs = find_prom_dbs(artifacts_dir)
    if not prom_dbs:
        logger.info("No Prometheus TSDB dumps found in %s, skipping replay.", artifacts_dir)
        return []

    groups = config.project.get_config("prom.capture.metrics.groups", {})
    if not groups:
        logger.info("No metrics groups configured, skipping replay.")
        return []

    include_dirs = config.project.get_config("prom.capture.metrics.include_dirs", [])

    raw_params = config.project.get_config("prom.capture.metrics.params", {})
    params = {
        k: config.project.resolve_reference(v) if isinstance(v, str) else v
        for k, v in raw_params.items()
    }

    existing_dirs = _find_existing_capture_dirs(artifacts_dir)

    groups_needing_replay: list[
        tuple[str, dict, list[Path], list[prom_metrics.MetricMetadata], dict[str, str], Path | None]
    ] = []

    for group_name, group_cfg in groups.items():
        if not group_cfg.get("enabled", True):
            logger.info("Group %s: disabled, skipping.", group_name)
            continue

        file_names = group_cfg.get("files", [])
        if not file_names:
            logger.warning("Group %s: no files specified, skipping.", group_name)
            continue

        try:
            yaml_paths = prom_metrics.resolve_files(file_names, include_dirs)
            metadata = prom_metrics.load_profile_metadata(*yaml_paths)
        except Exception:
            logger.exception("Group %s: failed to resolve metric profiles", group_name)
            continue

        if not metadata:
            logger.warning("Group %s: no metrics after loading, skipping.", group_name)
            continue

        capture_dir = existing_dirs.get(group_name)
        if not force_refresh and capture_dir and _check_group_cache(capture_dir, metadata):
            logger.info("Group %s: all %d metrics cached, skipping.", group_name, len(metadata))
            continue

        saved_vars = _read_saved_variables(capture_dir) if capture_dir else {}

        try:
            resolved_params = prom_metrics.resolve_params(params, None)
        except ValueError:
            if not saved_vars:
                logger.warning(
                    "Group %s: params contain set_at_runtime and no saved variables, skipping.",
                    group_name,
                )
                continue
            resolved_params = {k: v for k, v in params.items() if v != "set_at_runtime"}

        group_variables = prom_metrics.interpolate_variables(
            group_cfg.get("variables", {}),
            resolved_params,
        )
        if saved_vars:
            group_variables.update(saved_vars)

        groups_needing_replay.append(
            (group_name, group_cfg, yaml_paths, metadata, group_variables, capture_dir)
        )

    if not groups_needing_replay:
        logger.info("All metric groups cached, skipping Prometheus replay.")
        return []

    logger.info(
        "Replaying %d metric group(s): %s",
        len(groups_needing_replay),
        ", ".join(name for name, *_ in groups_needing_replay),
    )

    errors: list[str] = []

    with tempfile.TemporaryDirectory(prefix="forge-prom-replay-") as tmpdir:
        work_dir = Path(tmpdir)

        openmetrics_path = work_dir / "open_metrics.txt"
        logger.info("Decompressing %s ...", prom_dbs[0])
        _decompress_openmetrics(prom_dbs[0], openmetrics_path)

        tsdb_dir = work_dir / "tsdb"
        tsdb_dir.mkdir()
        _import_to_tsdb(openmetrics_path, tsdb_dir)
        openmetrics_path.unlink()

        prom_proc = _start_prometheus(tsdb_dir, work_dir)
        try:
            start_epoch, end_epoch = _get_time_bounds()
            logger.info(
                "TSDB time range: %d -> %d (%ds)", start_epoch, end_epoch, end_epoch - start_epoch
            )

            for (
                group_name,
                _group_cfg,
                yaml_paths,
                metadata,
                group_variables,
                capture_dir,
            ) in groups_needing_replay:
                try:
                    if capture_dir:
                        output_dir = capture_dir
                    else:
                        output_dir = (
                            artifacts_dir / f"replay__capture_prometheus_metrics_{group_name}"
                        )
                        output_dir.mkdir(parents=True, exist_ok=True)

                    kb_work = work_dir / f"kb_{group_name}"
                    kb_work.mkdir()

                    cleaned_profiles = _clean_profiles_for_kube_burner(
                        yaml_paths,
                        kb_work,
                        group_variables,
                    )
                    kb_config = _build_kube_burner_config(cleaned_profiles, output_dir)
                    _run_kube_burner(kb_config, start_epoch, end_epoch, group_variables)

                    prom_metrics.build_metadata_index(
                        metadata,
                        output_dir,
                        variables=group_variables,
                    )
                    logger.info("Group %s: replay completed, %d metrics", group_name, len(metadata))
                except Exception:
                    logger.exception("Group %s: replay failed", group_name)
                    errors.append(group_name)
        finally:
            _stop_prometheus(prom_proc)

    return errors
