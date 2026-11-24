"""How --model, the calling Claude model and agy's settings pick the Gemini model."""

from __future__ import annotations

import json

import pytest
from agylib import agybin

NEON_MEDIUM = {"model": "Gemini 4 Neon (Medium)"}


@pytest.mark.parametrize(
    ("name", "caller", "settings", "expected"),
    [
        (None, None, {}, None),
        (None, None, NEON_MEDIUM, "gemini-4-neon-medium"),
        (None, None, {"model": "Gemini 3.8 Flash (High)"}, "gemini-3.8-flash-high"),
        (None, None, {"model": "Orchestrator"}, None),
        (None, "claude-haiku-5-5", NEON_MEDIUM, "gemini-4-hydrogen-high"),
        (None, "claude-sonnet-5-5", {}, "gemini-4-hydrogen-high"),
        (None, "claude-opus-5-5[1m]", {}, "gemini-4-neon-high"),
        (None, "claude-fable-5-2", {}, "gemini-4-neon-high"),
        (None, "gpt-6", NEON_MEDIUM, "gemini-4-neon-medium"),
        ("high", None, {}, "gemini-4-neon-high"),
        ("low", "claude-sonnet-5-5", {}, "gemini-4-hydrogen-low"),
        ("xhigh", None, {"model": "Gemini 4 Carbon (High)"}, "gemini-4-carbon-xhigh"),
        ("low", None, {"model": "Gemini 3.8 Flash (High)"}, "gemini-3.8-flash-low"),
        ("high", None, {"model": "Orchestrator"}, "gemini-4-neon-high"),
        ("hydrogen", "claude-fable-5-2", {}, "gemini-4-hydrogen-high"),
        ("carbon-low", None, {}, "gemini-4-carbon-low"),
        ("carbon", None, {}, "gemini-4-carbon-medium"),
        ("argon", None, {}, "gemini-4-argon-medium"),
        ("gemini-3.1-pro-high", "claude-opus-5-5", {}, "gemini-3.1-pro-high"),
        ("neon-max", None, {}, "neon-max"),
        ("neon-", None, {}, "neon-"),
        ("Neon", None, {}, "gemini-4-neon-high"),
        ("Neon-High", None, {}, "gemini-4-neon-high"),
        ("XHigh", "claude-sonnet-5-5", {}, "gemini-4-hydrogen-xhigh"),
        ("High", None, {}, "gemini-4-neon-high"),
        ("unknown", None, {}, "gemini-4-unknown-medium"),
        (None, "octopus-opus-1", {"model": "Gemini 4 Hydrogen (Low)"}, "gemini-4-hydrogen-low"),
        (None, " Claude-Opus-5-5 ", {}, "gemini-4-neon-high"),
    ],
)
def test_resolve_model(name, caller, settings, expected):
    assert agybin.resolve_model(name, caller=caller, settings=settings) == expected


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        ("gemini-4-carbon-low", "gemini-4-carbon-high"),
        ("gemini-3.1-pro-low", "gemini-3.1-pro-high"),
        ("gemini-pro-orchestrator", "gemini-4-neon-high"),
    ],
)
def test_a_level_on_resume_keeps_the_job_family(current, expected):
    assert agybin.resolve_model("high", caller="claude-opus-5-5", current=current) == expected


def test_an_unreplaced_placeholder_falls_back_to_agy_settings():
    assert agybin.resolve_model(None, caller="<your model ID>", settings=NEON_MEDIUM) == "gemini-4-neon-medium"


def test_no_model_flag_when_agy_chooses(tmp_path):
    argv = agybin.build_command(tmp_path / "agy", model=None, mode=None, log_file=tmp_path / "log")
    assert "--model" not in argv


def test_agy_chooses_and_a_stall_resumes_without_a_model(companion):
    proc = companion.run("task", "--no-os-sandbox", "--stall-seconds", "3", "do it", scenario="stall")
    job = companion.job()
    assert job["status"] == "completed"
    first, second = companion.agy_calls()
    assert "--model" not in first["argv"]
    assert "--model" not in second["argv"]
    assert second["argv"][second["argv"].index("--conversation") + 1] == job["conversation_id"]
    assert job["model"] is None
    status = companion.run("status", job["id"]).stdout
    assert "Model: agy default" in status
    assert "None" not in status
    assert "None" not in proc.stdout


def test_caller_model_picks_the_gemini_model(companion):
    companion.run("task", "--no-os-sandbox", "--caller-model", "claude-sonnet-5-5", "do it")
    (call,) = companion.agy_calls()
    assert call["argv"][call["argv"].index("--model") + 1] == "gemini-4-hydrogen-high"


def test_agy_settings_model_is_the_default(companion, tmp_path):
    settings = tmp_path / "home" / ".gemini" / "antigravity-cli" / "settings.json"
    settings.write_text(json.dumps({"model": "Gemini 4 Hydrogen (Low)"}))
    companion.run("task", "--no-os-sandbox", "do it")
    (call,) = companion.agy_calls()
    assert call["argv"][call["argv"].index("--model") + 1] == "gemini-4-hydrogen-low"


def test_a_container_job_uses_the_container_sign_in_settings(contained, tmp_path):
    host = tmp_path / "home" / ".gemini" / "antigravity-cli" / "settings.json"
    host.write_text(json.dumps(NEON_MEDIUM))
    signin = tmp_path / "container-home" / ".gemini" / "antigravity-cli" / "settings.json"
    signin.write_text(json.dumps({"model": "Gemini 4 Hydrogen (Low)"}))
    contained.run("review", "--model", "high", scenario="review")
    assert contained.job()["model"] == "gemini-4-hydrogen-high"


def test_a_job_that_fails_before_agy_starts_shows_agy_default(companion):
    proc = companion.run("review", "--no-os-sandbox", scenario="auth", check=False)
    job = companion.job()
    assert job["status"] == "failed"
    assert job["model"] is None
    for text in (proc.stdout, companion.run("status").stdout, companion.run("status", job["id"]).stdout):
        assert "agy default" in text
        assert "None" not in text
