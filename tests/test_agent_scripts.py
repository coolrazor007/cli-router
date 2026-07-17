from pathlib import Path

import pytest
import yaml

from cli_router.models import DiscoveryProbe
from scripts.agent import detect_drift, validate_patch


def test_single_environment_removal_requires_explicit_confirmation(monkeypatch):
    monkeypatch.setattr(
        detect_drift,
        "MODEL_LIST_COMMANDS",
        {"grok": (["grok", "models"],)},
    )
    monkeypatch.setattr(detect_drift, "DEFAULT_MODELS", {"grok": ["grok-build"]})
    monkeypatch.setattr(
        detect_drift,
        "probe_models",
        lambda provider, runner, timeout: DiscoveryProbe(
            provider,
            ["grok", "models"],
            0,
            "grok-4.5\n",
            ["grok-4.5"],
        ),
    )

    verdict = detect_drift.detect(1.0)

    assert verdict["candidate_drift"] is True
    assert verdict["drift"] is False
    assert verdict["requires_confirmation"] is True


def test_confirmed_removal_is_actionable(monkeypatch):
    monkeypatch.setattr(
        detect_drift,
        "MODEL_LIST_COMMANDS",
        {"grok": (["grok", "models"],)},
    )
    monkeypatch.setattr(detect_drift, "DEFAULT_MODELS", {"grok": ["grok-build"]})
    monkeypatch.setattr(
        detect_drift,
        "probe_models",
        lambda provider, runner, timeout: DiscoveryProbe(
            provider,
            ["grok", "models"],
            0,
            "grok-4.5\n",
            ["grok-4.5"],
        ),
    )

    verdict = detect_drift.detect(1.0, confirm_removals=True)

    assert verdict["candidate_drift"] is True
    assert verdict["drift"] is True
    assert verdict["requires_confirmation"] is False


def test_patch_validator_accepts_only_expected_drift_files():
    validate_patch.validate_paths(
        ["cli_router/models.py", "tests/test_models.py", "CHANGELOG.md"]
    )


def test_patch_validator_rejects_workflow_or_extra_files():
    with pytest.raises(validate_patch.PatchValidationError, match="unexpected files"):
        validate_patch.validate_paths(
            ["cli_router/models.py", ".github/workflows/publish.yml"]
        )


def test_patch_validator_requires_all_expected_files():
    with pytest.raises(validate_patch.PatchValidationError, match="missing files"):
        validate_patch.validate_paths(["cli_router/models.py"])


def test_watchdog_isolates_generated_code_from_repository_write_token():
    workflow_path = Path(".github/workflows/agent-drift-watchdog.yml")
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]

    assert jobs["drift"]["permissions"] == {"contents": "read", "issues": "write"}
    assert jobs["verify_patch"]["permissions"] == {"contents": "read"}
    assert jobs["publish"]["runs-on"] == "ubuntu-latest"
    assert jobs["publish"]["permissions"]["contents"] == "write"

    drift_steps = jobs["drift"]["steps"]
    apply_step = next(step for step in drift_steps if step.get("name") == "Apply fix with local agent")
    assert "env -u GH_TOKEN -u GITHUB_TOKEN" in apply_step["run"]
    assert "github.token" not in apply_step.get("env", {}).values()

    workflow_text = workflow_path.read_text(encoding="utf-8")
    assert "gh pr merge" not in workflow_text
