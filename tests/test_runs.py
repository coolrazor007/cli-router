from pathlib import Path

import pytest
import yaml

from cli_router.config import RouterConfig
from cli_router.runs import list_runs, show_run


def make_config(tmp_path):
    return RouterConfig(
        {
            "version": 1,
            "defaults": {"run_dir": str(tmp_path / "runs")},
            "tools": {},
            "workflows": {},
        },
        None,
    )


def write_manifest(run_dir: Path, run_id: str, manifest: dict):
    path = run_dir / run_id
    path.mkdir(parents=True)
    (path / "run.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return path


def test_list_runs_returns_newest_first_and_marks_bad_manifests(tmp_path):
    config = make_config(tmp_path)
    root = tmp_path / "runs"
    write_manifest(
        root,
        "2026-07-07T10-00-00",
        {"workflow": "default", "exit_code": 1, "user_prompt": "older prompt", "stages": []},
    )
    write_manifest(
        root,
        "2026-07-07T11-00-00",
        {
            "workflow": "default",
            "exit_code": 0,
            "user_prompt": "newer prompt",
            "stages": [{"stage_id": "planner", "failure_kind": None}],
        },
    )
    bad = root / "2026-07-07T12-00-00"
    bad.mkdir()
    (bad / "run.yaml").write_text("not: [valid", encoding="utf-8")

    runs = list_runs(config)

    assert [run.id for run in runs] == [
        "2026-07-07T12-00-00",
        "2026-07-07T11-00-00",
        "2026-07-07T10-00-00",
    ]
    assert runs[0].workflow == "unknown"
    assert runs[0].exit_code is None
    assert runs[0].error == "invalid_manifest"
    assert runs[1].workflow == "default"
    assert runs[1].exit_code == 0
    assert runs[1].user_prompt == "newer prompt"
    assert runs[1].stages == [{"stage_id": "planner", "failure_kind": None}]


def test_show_run_resolves_partial_ids_and_lists_artifacts(tmp_path):
    config = make_config(tmp_path)
    path = write_manifest(
        tmp_path / "runs",
        "2026-07-07T11-00-00",
        {
            "workflow": "default",
            "exit_code": 0,
            "user_prompt": "inspect artifacts",
            "stages": [{"stage_id": "planner", "tool": "planner", "failure_kind": None}],
        },
    )
    (path / "planner.stdout").write_text("ok\n", encoding="utf-8")
    (path / "planner.stderr").write_text("", encoding="utf-8")

    detail = show_run(config, "2026-07-07T11")

    assert detail.id == "2026-07-07T11-00-00"
    assert detail.manifest["workflow"] == "default"
    assert detail.artifacts == ["planner.stderr", "planner.stdout", "run.yaml"]


def test_show_run_rejects_missing_or_ambiguous_ids(tmp_path):
    config = make_config(tmp_path)
    write_manifest(tmp_path / "runs", "2026-07-07T11-00-00", {"exit_code": 0})
    write_manifest(tmp_path / "runs", "2026-07-07T11-30-00", {"exit_code": 0})

    with pytest.raises(KeyError, match="Ambiguous run"):
        show_run(config, "2026-07-07T11")

    with pytest.raises(KeyError, match="Unknown run"):
        show_run(config, "missing")
