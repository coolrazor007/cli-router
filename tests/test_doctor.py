import subprocess

from cli_router.doctor import (
    Backend,
    ProviderHealth,
    candidate_backends,
    diagnose,
    extract_json_array,
    repair,
    run_agent,
)
from cli_router.modelcache import ModelCache


CODEX_CATALOG = '{"models":[{"slug":"gpt-5.6-sol","visibility":"list"},{"slug":"gpt-5.5","visibility":"list"}]}'
GROK_DRIFT = "You are logged in with grok.com.\nSomething entirely new here.\n"


def _list_runner(outputs):
    """For discovery: maps a command tuple -> (returncode, stdout)."""

    def runner(command, **kwargs):
        returncode, stdout = outputs.get(tuple(command), (1, ""))
        return subprocess.CompletedProcess(command, returncode, stdout, "")

    return runner


def _drifting(provider="grok"):
    return ProviderHealth(
        provider, True, False, [], "none", "did not parse", raw_output=GROK_DRIFT, command=[provider, "models"]
    )


# --- diagnose -------------------------------------------------------------


def test_diagnose_reports_healthy_missing_and_drifting():
    runner = _list_runner(
        {
            ("codex", "debug", "models"): (0, CODEX_CATALOG),
            ("grok", "models"): (0, GROK_DRIFT),
        }
    )
    present = {"codex", "grok"}  # claude is not installed
    which = lambda exe: "/usr/bin/" + exe if exe in present else None

    health = {h.provider: h for h in diagnose(("codex", "grok", "claude"), runner=runner, which=which)}

    assert health["codex"].healthy and health["codex"].source == "catalog"
    assert health["codex"].models == ["gpt-5.6-sol", "gpt-5.5"]
    assert health["grok"].drifting and health["grok"].raw_output == GROK_DRIFT
    assert not health["claude"].cli_present and not health["claude"].drifting


def test_diagnose_marks_provider_without_discovery_command_as_static():
    # claude/hermes have no safe discovery command; they must report as
    # "static" (not "drift") and must never shell out.
    def exploding_runner(command, **kwargs):
        raise AssertionError(f"static provider must not be probed: {command}")

    health = {
        h.provider: h
        for h in diagnose(("claude", "hermes"), runner=exploding_runner, which=lambda exe: "/usr/bin/" + exe)
    }

    assert health["claude"].source == "static"
    assert health["claude"].models[0] == "claude-fable-5"
    assert not health["claude"].drifting
    assert health["hermes"].source == "static" and health["hermes"].models == ["hermes-auto"]


def test_diagnose_uses_a_long_discovery_timeout():
    seen = {}

    def runner(command, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(command, 0, CODEX_CATALOG, "")

    diagnose(("codex",), runner=runner, which=lambda exe: "/usr/bin/" + exe)

    assert seen["timeout"] == 10.0  # longer than the interactive 1.5s picker


# --- candidate_backends ---------------------------------------------------


def test_candidate_backends_are_sorted_cache_first_and_skip_missing():
    cache = ModelCache({"grok": ["grok-4.5"]})
    present = {"codex", "grok"}  # claude, hermes not installed
    which = lambda exe: "/usr/bin/" + exe if exe in present else None

    backends = candidate_backends(("codex", "grok", "claude", "hermes"), cache=cache, which=which)
    labels = [b.label for b in backends]

    # codex before grok (alphabetical); grok's cached model precedes static ones.
    assert labels[0].startswith("codex:gpt-5.6-sol")
    assert "grok:grok-4.5" in labels
    assert labels.index("grok:grok-4.5") < labels.index("grok:grok-build")
    assert not any(label.startswith(("claude:", "hermes:")) for label in labels)


# --- repair with backend failover ----------------------------------------


def _agent_runner(behavior):
    """For agent calls: maps argv[0] (provider exe) -> (returncode, stdout)."""

    def runner(command, **kwargs):
        returncode, stdout = behavior.get(command[0], (127, ""))
        return subprocess.CompletedProcess(command, returncode, stdout, "boom")

    return runner


def test_repair_fails_over_to_a_working_backend(tmp_path):
    health = [_drifting("grok")]
    cache = ModelCache(path=tmp_path / "cache.yaml")
    # First two candidates (claude) are broken; codex answers.
    backends = [Backend("claude", "m1"), Backend("claude", "m2"), Backend("codex", "gpt-5.6-sol")]
    runner = _agent_runner(
        {
            "claude": (1, ""),
            "codex": (0, '["grok-4.5","grok-composer-2.5-fast"]'),
        }
    )

    reports = repair(health, cache=cache, backends=backends, runner=runner)

    assert [(r.provider, r.ok, r.doctor) for r in reports] == [("grok", True, "codex")]
    assert cache.get("grok") == ["grok-4.5", "grok-composer-2.5-fast"]
    assert ModelCache.load(tmp_path / "cache.yaml").get("grok") == ["grok-4.5", "grok-composer-2.5-fast"]


def test_repair_pins_working_backend_across_providers(tmp_path):
    calls = []

    def runner(command, **kwargs):
        calls.append(command[0])
        if command[0] == "claude":
            return subprocess.CompletedProcess(command, 1, "", "down")
        return subprocess.CompletedProcess(command, 0, '["m-1","m-2"]', "")

    health = [_drifting("grok"), _drifting("hermes")]
    backends = [Backend("claude", "x"), Backend("codex", "gpt-5.6-sol")]
    cache = ModelCache(path=tmp_path / "c.yaml")

    repair(health, cache=cache, backends=backends, runner=runner)

    # claude is tried once (fails), codex pins; the 2nd provider reuses codex
    # without re-trying claude.
    assert calls == ["claude", "codex", "codex"]


def test_repair_filters_agent_hallucinations(tmp_path):
    runner = _agent_runner({"codex": (0, '["grok-4.5","You","not a model!","logged"]')})
    cache = ModelCache(path=tmp_path / "c.yaml")

    repair([_drifting("grok")], cache=cache, backends=[Backend("codex", "gpt-5.6-sol")], runner=runner)

    assert cache.get("grok") == ["grok-4.5"]


def test_repair_without_any_backend_reports_failure(tmp_path):
    cache = ModelCache(path=tmp_path / "c.yaml")
    reports = repair([_drifting("grok")], cache=cache, backends=[], runner=_agent_runner({}))
    assert [(r.provider, r.ok) for r in reports] == [("grok", False)]
    assert "no agent CLI" in reports[0].detail


def test_repair_when_all_backends_fail_reports_failure(tmp_path):
    runner = _agent_runner({"codex": (1, "")})
    cache = ModelCache(path=tmp_path / "c.yaml")
    reports = repair([_drifting("grok")], cache=cache, backends=[Backend("codex", "x")], runner=runner)
    assert reports[0].ok is False
    assert cache.get("grok") == []


def test_repair_can_be_cancelled(tmp_path):
    runner = _agent_runner({"codex": (0, '["m-1"]')})
    cache = ModelCache(path=tmp_path / "c.yaml")

    reports = repair(
        [_drifting("grok")],
        cache=cache,
        backends=[Backend("codex", "x")],
        runner=runner,
        cancelled=lambda: True,
    )

    assert reports[0].ok is False and "cancelled" in reports[0].detail
    assert cache.get("grok") == []


def test_repair_noop_when_nothing_drifting():
    health = [ProviderHealth("codex", True, True, ["gpt-5.6-sol"], "catalog", "ok")]
    assert repair(health, cache=ModelCache(), backends=[Backend("codex")], runner=_agent_runner({})) == []


# --- run_agent ------------------------------------------------------------


def test_run_agent_routes_model_and_returns_stdout():
    seen = {}

    def runner(command, **kwargs):
        seen["argv"] = command
        return subprocess.CompletedProcess(command, 0, "hello", "")

    out = run_agent(Backend("codex", "gpt-5.6-sol"), "PROMPT", runner)

    assert out == "hello"
    assert seen["argv"] == ["codex", "exec", "-m", "gpt-5.6-sol", "PROMPT"]


# --- extract_json_array ---------------------------------------------------


def test_extract_json_array_handles_bare_array():
    assert extract_json_array('["gpt-5.6-sol", "gpt-5.5"]') == ["gpt-5.6-sol", "gpt-5.5"]


def test_extract_json_array_ignores_surrounding_prose_and_fences():
    text = 'Sure! Here are the models:\n```json\n["a-1", "b-2"]\n```\nLet me know if you need more.'
    assert extract_json_array(text) == ["a-1", "b-2"]


def test_extract_json_array_unwraps_models_object():
    assert extract_json_array('{"models": ["m-1", "m-2"]}') == ["m-1", "m-2"]


def test_extract_json_array_unwraps_result_string():
    assert extract_json_array('{"result": "[\\"g-4.5\\"]", "cost": 0.01}') == ["g-4.5"]


def test_extract_json_array_returns_empty_when_absent():
    assert extract_json_array("no models to be found here") == []
