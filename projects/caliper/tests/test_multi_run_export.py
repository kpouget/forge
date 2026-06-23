"""
Tests for the multi-run caliper export pipeline.

Covers:
- Run directory auto-detection via runs/ convention
- Shared vs run-specific file partitioning
- metrics.json / parameters.json reading and MLflow logging
- Parent + nested child run creation
- Single-run fallback when no runs/ directory exists
- Workspace propagation
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from projects.caliper.orchestration.export import (
    METRICS_FILE,
    PARAMETERS_FILE,
    _discover_run_dirs,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def artifact_tree(tmp_path: Path) -> Path:
    """Build a realistic Fournos-style artifact tree with two test runs."""
    base = tmp_path / "artifacts"

    # Shared phase directories
    pre_cleanup = base / "000__pre-cleanup"
    pre_cleanup.mkdir(parents=True)
    (pre_cleanup / "run.log").write_text("pre-cleanup log")

    prepare = base / "001__prepare"
    prepare.mkdir(parents=True)
    (prepare / "run.log").write_text("prepare log")
    (prepare / "config.yaml").write_text("prepare: true")

    # Test phase with runs/ subdirectory
    test_phase = base / "002__test"
    test_phase.mkdir(parents=True)
    (test_phase / "config.yaml").write_text("test: true")
    (test_phase / "run.log").write_text("test log")

    runs = test_phase / "runs"
    runs.mkdir()

    # Run A
    run_a = runs / "mcp-smoke-s1-u16-gateway"
    run_a.mkdir()
    (run_a / METRICS_FILE).write_text(
        json.dumps(
            {
                "total_requests": 1000,
                "total_failures": 5,
                "failure_rate": 0.005,
                "avg_response_time_ms": 45.2,
                "p95_ms": 120.0,
                "requests_per_second": 31.5,
            }
        )
    )
    (run_a / PARAMETERS_FILE).write_text(
        json.dumps(
            {
                "preset": "smoke",
                "target": "gateway",
                "users": 16,
                "num_servers": 1,
            }
        )
    )
    (run_a / "stats.csv").write_text("Type,Name,Request Count\nPOST,/mcp,1000")
    pod_logs_a = run_a / "pod_logs"
    pod_logs_a.mkdir()
    (pod_logs_a / "gateway.log").write_text("gateway log A")

    # Run B
    run_b = runs / "mcp-smoke-s1-u64-gateway"
    run_b.mkdir()
    (run_b / METRICS_FILE).write_text(
        json.dumps(
            {
                "total_requests": 5000,
                "total_failures": 10,
                "failure_rate": 0.002,
                "avg_response_time_ms": 80.1,
                "p95_ms": 250.0,
                "requests_per_second": 55.2,
            }
        )
    )
    (run_b / PARAMETERS_FILE).write_text(
        json.dumps(
            {
                "preset": "smoke",
                "target": "gateway",
                "users": 64,
                "num_servers": 1,
            }
        )
    )
    (run_b / "stats.csv").write_text("Type,Name,Request Count\nPOST,/mcp,5000")

    # Export artifacts phase (shared)
    export = base / "003__export-artifacts"
    export.mkdir(parents=True)
    (export / "run.log").write_text("export log")

    return base


@pytest.fixture()
def single_run_tree(tmp_path: Path) -> Path:
    """Artifact tree without runs/ — should trigger single-run fallback."""
    base = tmp_path / "artifacts"

    prepare = base / "001__prepare"
    prepare.mkdir(parents=True)
    (prepare / "run.log").write_text("prepare log")

    test_phase = base / "002__test"
    test_phase.mkdir(parents=True)
    (test_phase / "results.csv").write_text("some results")

    return base


# ---------------------------------------------------------------------------
# Test: _discover_run_dirs
# ---------------------------------------------------------------------------


class TestDiscoverRunDirs:
    def test_detects_runs_subdirectories(self, artifact_tree: Path):
        run_dirs = _discover_run_dirs(artifact_tree)

        assert len(run_dirs) == 2
        names = [d.name for d in run_dirs]
        assert "mcp-smoke-s1-u16-gateway" in names
        assert "mcp-smoke-s1-u64-gateway" in names

    def test_returns_empty_when_no_runs_dir(self, single_run_tree: Path):
        run_dirs = _discover_run_dirs(single_run_tree)
        assert run_dirs == []

    def test_returns_empty_for_empty_runs_dir(self, tmp_path: Path):
        base = tmp_path / "artifacts" / "002__test" / "runs"
        base.mkdir(parents=True)
        run_dirs = _discover_run_dirs(tmp_path / "artifacts")
        assert run_dirs == []

    def test_ignores_files_named_runs(self, tmp_path: Path):
        base = tmp_path / "artifacts"
        base.mkdir(parents=True)
        (base / "runs").write_text("I am a file, not a directory")
        run_dirs = _discover_run_dirs(base)
        assert run_dirs == []

    def test_returns_sorted(self, artifact_tree: Path):
        run_dirs = _discover_run_dirs(artifact_tree)
        names = [d.name for d in run_dirs]
        assert names == sorted(names)


# ---------------------------------------------------------------------------
# Test: shared vs run-specific file partitioning
# ---------------------------------------------------------------------------


class TestFilePartitioning:
    def test_shared_files_exclude_run_dirs(self, artifact_tree: Path):
        run_dirs = _discover_run_dirs(artifact_tree)

        shared_paths = [
            p
            for p in artifact_tree.rglob("*")
            if p.is_file() and not any(p.resolve().is_relative_to(rd.resolve()) for rd in run_dirs)
        ]

        shared_names = {p.name for p in shared_paths}
        assert "run.log" in shared_names
        assert "config.yaml" in shared_names

        # Run-specific files must NOT be in shared
        assert "stats.csv" not in shared_names
        assert METRICS_FILE not in shared_names
        assert PARAMETERS_FILE not in shared_names

    def test_run_specific_files_are_correct(self, artifact_tree: Path):
        run_dirs = _discover_run_dirs(artifact_tree)
        run_a = [d for d in run_dirs if "u16" in d.name][0]
        run_a_files = {p.name for p in run_a.rglob("*") if p.is_file()}

        assert METRICS_FILE in run_a_files
        assert PARAMETERS_FILE in run_a_files
        assert "stats.csv" in run_a_files
        assert "gateway.log" in run_a_files


# ---------------------------------------------------------------------------
# Test: metrics.json / parameters.json loading
# ---------------------------------------------------------------------------


class TestMetricsParametersLoading:
    def test_load_metrics_json(self, artifact_tree: Path):
        from projects.caliper.engine.file_export.mlflow_backend import _load_json_file

        run_dirs = _discover_run_dirs(artifact_tree)
        run_a = [d for d in run_dirs if "u16" in d.name][0]

        metrics = _load_json_file(run_a / METRICS_FILE)
        assert metrics["total_requests"] == 1000
        assert metrics["p95_ms"] == 120.0
        assert metrics["requests_per_second"] == 31.5

    def test_load_parameters_json(self, artifact_tree: Path):
        from projects.caliper.engine.file_export.mlflow_backend import _load_json_file

        run_dirs = _discover_run_dirs(artifact_tree)
        run_a = [d for d in run_dirs if "u16" in d.name][0]

        params = _load_json_file(run_a / PARAMETERS_FILE)
        assert params["preset"] == "smoke"
        assert params["users"] == 16
        assert params["target"] == "gateway"

    def test_load_missing_file_returns_empty(self, tmp_path: Path):
        from projects.caliper.engine.file_export.mlflow_backend import _load_json_file

        result = _load_json_file(tmp_path / "nonexistent.json")
        assert result == {}

    def test_load_invalid_json_returns_empty(self, tmp_path: Path):
        from projects.caliper.engine.file_export.mlflow_backend import _load_json_file

        bad = tmp_path / "bad.json"
        bad.write_text("{invalid json")
        result = _load_json_file(bad)
        assert result == {}


# ---------------------------------------------------------------------------
# Test: MLflow multi-run logging (mocked)
# ---------------------------------------------------------------------------


class TestMultiRunMlflowLogging:
    """Test that log_multi_run_artifacts creates the correct MLflow structure."""

    @pytest.fixture()
    def mock_mlflow(self):
        mock_ml = MagicMock()

        mock_active = MagicMock()
        mock_active.info.run_id = "active-run-id"
        mock_ml.active_run.return_value = mock_active

        mock_parent_run = MagicMock()
        mock_parent_run.info.run_id = "parent-run-id"
        mock_ml.start_run.return_value.__enter__ = MagicMock(return_value=mock_parent_run)
        mock_ml.start_run.return_value.__exit__ = MagicMock(return_value=False)
        mock_ml.get_tracking_uri.return_value = "http://test-mlflow:5000"

        mock_client = MagicMock()
        mock_ml.tracking.MlflowClient.return_value = mock_client

        with patch.dict("sys.modules", {"mlflow": mock_ml, "mlflow.tracking": mock_ml.tracking}):
            yield mock_ml

    def test_creates_parent_and_child_runs(self, artifact_tree: Path, mock_mlflow):
        from projects.caliper.engine.file_export.mlflow_backend import (
            log_multi_run_artifacts,
        )

        run_dirs = _discover_run_dirs(artifact_tree)
        shared_paths = [
            p
            for p in artifact_tree.rglob("*")
            if p.is_file() and not any(p.resolve().is_relative_to(rd.resolve()) for rd in run_dirs)
        ]

        log_multi_run_artifacts(
            shared_paths=shared_paths,
            shared_artifact_root=artifact_tree,
            run_dirs=run_dirs,
            metrics_file=METRICS_FILE,
            parameters_file=PARAMETERS_FILE,
            tracking_uri="http://test-mlflow:5000",
            experiment="test-experiment",
            parent_run_name="test-parent",
        )

        # Parent run should be created with run_name
        start_calls = mock_mlflow.start_run.call_args_list
        assert any(
            c.kwargs.get("run_name") == "test-parent" or (c.args and c.args[0] == "test-parent")
            for c in start_calls
        ), f"Parent run_name not found in calls: {start_calls}"

        # Nested child runs should be created
        nested_calls = [c for c in start_calls if c.kwargs.get("nested") is True]
        assert len(nested_calls) == 2

        child_names = sorted(c.kwargs.get("run_name") for c in nested_calls)
        assert child_names == ["mcp-smoke-s1-u16-gateway", "mcp-smoke-s1-u64-gateway"]

    def test_logs_metrics_from_json(self, artifact_tree: Path, mock_mlflow):
        from projects.caliper.engine.file_export.mlflow_backend import (
            log_multi_run_artifacts,
        )

        run_dirs = _discover_run_dirs(artifact_tree)

        log_multi_run_artifacts(
            shared_paths=[],
            shared_artifact_root=artifact_tree,
            run_dirs=run_dirs,
            metrics_file=METRICS_FILE,
            parameters_file=PARAMETERS_FILE,
            tracking_uri="http://test-mlflow:5000",
            experiment="test-experiment",
        )

        metric_calls = mock_mlflow.log_metric.call_args_list
        metric_keys = {c.args[0] for c in metric_calls}
        assert "total_requests" in metric_keys
        assert "p95_ms" in metric_keys
        assert "requests_per_second" in metric_keys

    def test_logs_parameters_from_json(self, artifact_tree: Path, mock_mlflow):
        from projects.caliper.engine.file_export.mlflow_backend import (
            log_multi_run_artifacts,
        )

        run_dirs = _discover_run_dirs(artifact_tree)

        log_multi_run_artifacts(
            shared_paths=[],
            shared_artifact_root=artifact_tree,
            run_dirs=run_dirs,
            metrics_file=METRICS_FILE,
            parameters_file=PARAMETERS_FILE,
            tracking_uri="http://test-mlflow:5000",
            experiment="test-experiment",
        )

        param_calls = mock_mlflow.log_param.call_args_list
        param_keys = {c.args[0] for c in param_calls}
        assert "preset" in param_keys
        assert "users" in param_keys
        assert "target" in param_keys

    def test_sets_experiment(self, artifact_tree: Path, mock_mlflow):
        from projects.caliper.engine.file_export.mlflow_backend import (
            log_multi_run_artifacts,
        )

        log_multi_run_artifacts(
            shared_paths=[],
            shared_artifact_root=artifact_tree,
            run_dirs=_discover_run_dirs(artifact_tree),
            metrics_file=METRICS_FILE,
            parameters_file=PARAMETERS_FILE,
            tracking_uri="http://test-mlflow:5000",
            experiment="my-experiment",
        )

        mock_mlflow.set_experiment.assert_called_once_with("my-experiment")

    def test_sets_forge_tags_on_parent(self, artifact_tree: Path, mock_mlflow):
        from projects.caliper.engine.file_export.mlflow_backend import (
            log_multi_run_artifacts,
        )

        run_dirs = _discover_run_dirs(artifact_tree)

        log_multi_run_artifacts(
            shared_paths=[],
            shared_artifact_root=artifact_tree,
            run_dirs=run_dirs,
            metrics_file=METRICS_FILE,
            parameters_file=PARAMETERS_FILE,
            tracking_uri="http://test-mlflow:5000",
            experiment="test",
        )

        tag_calls = mock_mlflow.set_tag.call_args_list
        tag_dict = {c.args[0]: c.args[1] for c in tag_calls}
        assert tag_dict.get("forge.multi_run") == "true"
        assert tag_dict.get("forge.child_count") == "2"


# ---------------------------------------------------------------------------
# Test: workspace propagation
# ---------------------------------------------------------------------------


class TestWorkspacePropagation:
    @pytest.fixture()
    def _mock_mlflow(self):
        mock_ml = MagicMock()
        mock_run = MagicMock()
        mock_run.info.run_id = "test-id"
        mock_ml.start_run.return_value.__enter__ = MagicMock(return_value=mock_run)
        mock_ml.start_run.return_value.__exit__ = MagicMock(return_value=False)
        mock_ml.active_run.return_value = mock_run
        mock_ml.get_tracking_uri.return_value = "http://test:5000"
        mock_ml.tracking.MlflowClient.return_value = MagicMock()
        with patch.dict("sys.modules", {"mlflow": mock_ml, "mlflow.tracking": mock_ml.tracking}):
            yield mock_ml

    def test_workspace_restored_after_call(self, artifact_tree: Path, _mock_mlflow):
        from projects.caliper.engine.file_export.mlflow_backend import (
            log_multi_run_artifacts,
        )

        os.environ.pop("MLFLOW_WORKSPACE", None)
        log_multi_run_artifacts(
            shared_paths=[],
            shared_artifact_root=artifact_tree,
            run_dirs=_discover_run_dirs(artifact_tree),
            metrics_file=METRICS_FILE,
            parameters_file=PARAMETERS_FILE,
            tracking_uri="http://test:5000",
            experiment="test",
            workspace="ashtarkb",
        )
        assert "MLFLOW_WORKSPACE" not in os.environ

    def test_no_workspace_leaves_env_unchanged(self, artifact_tree: Path, _mock_mlflow):
        from projects.caliper.engine.file_export.mlflow_backend import (
            log_multi_run_artifacts,
        )

        os.environ.pop("MLFLOW_WORKSPACE", None)
        log_multi_run_artifacts(
            shared_paths=[],
            shared_artifact_root=artifact_tree,
            run_dirs=_discover_run_dirs(artifact_tree),
            metrics_file=METRICS_FILE,
            parameters_file=PARAMETERS_FILE,
            tracking_uri="http://test:5000",
            experiment="test",
            workspace=None,
        )
        assert "MLFLOW_WORKSPACE" not in os.environ


# ---------------------------------------------------------------------------
# Test: helpers/summary.py
# ---------------------------------------------------------------------------


class TestSummaryHelpers:
    def test_save_metrics(self, tmp_path: Path):
        from projects.agentic_tools.locust.helpers.parse_results import RunMetrics
        from projects.agentic_tools.locust.helpers.summary import save_metrics

        metrics = RunMetrics(
            total_requests=1000,
            total_failures=5,
            failure_rate=0.005,
            avg_response_time_ms=45.2,
            p50_ms=40.0,
            p90_ms=80.0,
            p95_ms=120.0,
            p99_ms=200.0,
            max_ms=500.0,
            requests_per_second=31.5,
        )

        path = save_metrics(metrics, tmp_path)

        assert path == tmp_path / "metrics.json"
        assert path.exists()

        data = json.loads(path.read_text())
        assert data["total_requests"] == 1000
        assert data["p95_ms"] == 120.0
        assert data["requests_per_second"] == 31.5
        assert data["failure_rate"] == 0.005

    def test_save_parameters(self, tmp_path: Path):
        from projects.agentic_tools.locust.helpers.summary import save_parameters

        path = save_parameters(
            tmp_path,
            preset="smoke",
            target="gateway",
            users=16,
            num_servers=1,
            version=None,
        )

        assert path == tmp_path / "parameters.json"
        assert path.exists()

        data = json.loads(path.read_text())
        assert data["preset"] == "smoke"
        assert data["users"] == 16
        assert data["version"] == ""  # None → empty string


# ---------------------------------------------------------------------------
# Test: helpers/parse_results.py
# ---------------------------------------------------------------------------


class TestParseResults:
    def test_parse_stats_csv(self):
        from projects.agentic_tools.locust.helpers.parse_results import parse_stats_csv

        csv_data = (
            "Type,Name,Request Count,Failure Count,Average Response Time,"
            "50%,90%,95%,99%,Max Response Time,Requests/s\n"
            "POST,/mcp/tools,800,2,42.5,35,70,90,150,300,25.0\n"
            "GET,/health,200,0,5.0,4,8,10,15,20,6.5\n"
            ",Aggregated,1000,2,33.5,30,60,80,120,300,31.5\n"
        )

        metrics = parse_stats_csv(csv_data)

        assert metrics.total_requests == 1000
        assert metrics.total_failures == 2
        assert metrics.avg_response_time_ms == 33.5
        assert metrics.p95_ms == 80.0
        assert metrics.requests_per_second == 31.5
        assert len(metrics.per_request_metrics) == 2
        assert "POST:/mcp/tools" in metrics.per_request_metrics


# ---------------------------------------------------------------------------
# Test: single-run fallback
# ---------------------------------------------------------------------------


class TestSingleRunFallback:
    def test_no_runs_dir_returns_empty(self, single_run_tree: Path):
        run_dirs = _discover_run_dirs(single_run_tree)
        assert run_dirs == []

    def test_single_run_dir_uses_flat_export(self, tmp_path: Path):
        """When only one run directory exists, export should use single-run
        (flat) mode instead of creating a parent with one nested child."""
        base = tmp_path / "artifacts"
        test_phase = base / "002__test"
        runs = test_phase / "runs"
        run_a = runs / "mcp-smoke-s1-u16-gateway"
        run_a.mkdir(parents=True)
        (run_a / METRICS_FILE).write_text(json.dumps({"total_requests": 100}))
        (run_a / PARAMETERS_FILE).write_text(json.dumps({"preset": "smoke"}))

        run_dirs = _discover_run_dirs(base)
        assert len(run_dirs) == 1

        # The routing condition in run_from_orchestration_config is:
        #   if len(run_dirs) > 1  → multi-run export
        #   else                  → single-run (flat) export
        # Verify the threshold: a single run dir must NOT trigger multi-run.
        assert not (len(run_dirs) > 1), (
            "Single run dir should fall through to flat export, not multi-run"
        )
