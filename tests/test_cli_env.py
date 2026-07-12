"""CLI environment-variable and .env configuration."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

import doku.cli as cli

FIXTURE_REPO = str(Path(__file__).parent / "fixtures" / "sample_spring_app")

CREDS = {"DOKU_API_KEY": "test-key", "DOKU_API_BASE": "https://llm.example.com/api/v1"}

runner = CliRunner()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        "DOKU_API_KEY",
        "DOKU_API_BASE",
        "DOKU_MODEL",
        "DOKU_CHAT_COMPLETIONS",
        "DOKU_MODEL_RPS",
        "DOKU_MODEL_BURST",
        "DOKU_MODEL_MAX_RETRIES",
    ):
        monkeypatch.delenv(name, raising=False)


class _StopAtBuild(Exception):
    """Raised by the stubbed workflow builder: configuration was resolved
    and the model/credentials resolved — everything past this point needs an
    LLM."""


@pytest.fixture
def captured_build(monkeypatch):
    captured = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        raise _StopAtBuild

    monkeypatch.setattr(cli, "build_workflow_agent", fake_build)
    return captured


def _run(captured_build, args, env=None):
    result = runner.invoke(cli.app, args, env={**CREDS, **(env or {})})
    assert isinstance(result.exception, _StopAtBuild), result.output
    return captured_build


def test_stops_without_model(captured_build, tmp_path):
    result = runner.invoke(cli.app, [FIXTURE_REPO, "--out", str(tmp_path)], env=CREDS)
    assert result.exit_code == 1
    assert "No model configured: pass --model or set DOKU_MODEL" in result.output
    assert not captured_build, "must stop before building the orchestrator"


def test_model_from_env_var(captured_build, tmp_path):
    captured = _run(
        captured_build,
        [FIXTURE_REPO, "--out", str(tmp_path)],
        env={"DOKU_MODEL": "anthropic:claude-sonnet-5"},
    )
    assert captured["model"] == "anthropic:claude-sonnet-5"


def test_flag_overrides_env_var(captured_build, tmp_path):
    captured = _run(
        captured_build,
        [FIXTURE_REPO, "--out", str(tmp_path), "--model", "openrouter:from-flag"],
        env={"DOKU_MODEL": "openrouter:from-env"},
    )
    assert captured["model"] == "openrouter:from-flag"


def test_chat_completions_defaults_off_and_env_enables_it(captured_build, tmp_path):
    captured = _run(
        captured_build,
        [FIXTURE_REPO, "--out", str(tmp_path)],
        env={"DOKU_MODEL": "openai:m"},
    )
    assert captured["chat_completions"] is False

    captured_build.clear()
    captured = _run(
        captured_build,
        [FIXTURE_REPO, "--out", str(tmp_path)],
        env={"DOKU_MODEL": "openai:m", "DOKU_CHAT_COMPLETIONS": "1"},
    )
    assert captured["chat_completions"] is True


def test_model_tuning_disabled_by_default(captured_build, tmp_path):
    captured = _run(captured_build, [FIXTURE_REPO, "--out", str(tmp_path)], env={"DOKU_MODEL": "openai:m"})
    assert captured["model_rps"] is None
    assert captured["model_burst"] == 1
    assert captured["max_retries"] is None


def test_max_retries_from_env(captured_build, tmp_path):
    captured = _run(
        captured_build,
        [FIXTURE_REPO, "--out", str(tmp_path)],
        env={"DOKU_MODEL": "openai:m", "DOKU_MODEL_MAX_RETRIES": "0"},
    )
    assert captured["max_retries"] == 0


def test_rate_limit_from_env(captured_build, tmp_path):
    captured = _run(
        captured_build,
        [FIXTURE_REPO, "--out", str(tmp_path)],
        env={"DOKU_MODEL": "openai:m", "DOKU_MODEL_RPS": "0.5", "DOKU_MODEL_BURST": "3"},
    )
    assert captured["model_rps"] == 0.5
    assert captured["model_burst"] == 3


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"DOKU_MODEL_RPS": "fast"}, "DOKU_MODEL_RPS must be a positive number, got 'fast'."),
        ({"DOKU_MODEL_RPS": "0"}, "DOKU_MODEL_RPS must be a positive number, got '0'."),
        ({"DOKU_MODEL_RPS": "1", "DOKU_MODEL_BURST": "0"}, "DOKU_MODEL_BURST must be a positive integer, got '0'."),
        ({"DOKU_MODEL_BURST": "5"}, "DOKU_MODEL_BURST requires DOKU_MODEL_RPS to be set."),
        ({"DOKU_MODEL_MAX_RETRIES": "-1"}, "DOKU_MODEL_MAX_RETRIES must be a non-negative integer, got '-1'."),
        ({"DOKU_MODEL_MAX_RETRIES": "many"}, "DOKU_MODEL_MAX_RETRIES must be a non-negative integer, got 'many'."),
    ],
)
def test_stops_on_invalid_rate_limit(captured_build, tmp_path, env, message):
    result = runner.invoke(
        cli.app, [FIXTURE_REPO, "--out", str(tmp_path)], env={**CREDS, "DOKU_MODEL": "openai:m", **env}
    )
    assert result.exit_code == 1
    assert message in result.output
    assert not captured_build, "must stop before building the orchestrator"


def test_credentials_are_passed_to_build(captured_build, tmp_path):
    captured = _run(
        captured_build,
        [FIXTURE_REPO, "--out", str(tmp_path)],
        env={"DOKU_MODEL": "openrouter:some-model"},
    )
    assert captured["api_key"] == CREDS["DOKU_API_KEY"]
    assert captured["api_base"] == CREDS["DOKU_API_BASE"]


@pytest.mark.parametrize(
    ("env", "expected_missing"),
    [
        ({}, "DOKU_API_KEY, DOKU_API_BASE"),
        ({"DOKU_API_BASE": "https://llm.example.com"}, "DOKU_API_KEY"),
        ({"DOKU_API_KEY": "test-key"}, "DOKU_API_BASE"),
        ({"DOKU_API_KEY": "", "DOKU_API_BASE": ""}, "DOKU_API_KEY, DOKU_API_BASE"),
    ],
)
def test_stops_when_credentials_missing(captured_build, tmp_path, env, expected_missing):
    result = runner.invoke(cli.app, [FIXTURE_REPO, "--out", str(tmp_path)], env=env)
    assert result.exit_code == 1
    assert f"Missing required environment variable(s): {expected_missing}." in result.output
    assert not captured_build, "must stop before building the orchestrator"


def test_reports_all_missing_configuration_at_once(captured_build, tmp_path):
    result = runner.invoke(cli.app, [FIXTURE_REPO, "--out", str(tmp_path)], env={})
    assert result.exit_code == 1
    assert "DOKU_API_KEY, DOKU_API_BASE" in result.output
    assert "No model configured" in result.output


def _run_cli_subprocess(cwd, extra_env=None):
    """Run the real CLI in a subprocess so the import-time load_dotenv sees
    `cwd` as the working directory. All DOKU_* vars are stripped first."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("DOKU_")}
    return subprocess.run(
        [sys.executable, "-m", "doku.cli", FIXTURE_REPO, "--out", str(cwd / "docs")],
        cwd=cwd,
        env={**env, **(extra_env or {})},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_dotenv_file_is_loaded(tmp_path):
    # .env supplies key and base URL but no model: getting exactly (and only)
    # the model error proves the .env was read.
    (tmp_path / ".env").write_text(
        "DOKU_API_KEY=from-dotenv\nDOKU_API_BASE=https://llm.example.com/api/v1\n"
    )
    result = _run_cli_subprocess(tmp_path)
    assert result.returncode == 1
    assert "No model configured" in result.stderr
    assert "Missing required environment variable(s)" not in result.stderr


def test_real_environment_wins_over_dotenv(tmp_path):
    # .env has everything, but the real environment blanks the key -> only
    # the key is reported missing (base URL and model still come from .env).
    (tmp_path / ".env").write_text(
        "DOKU_API_KEY=from-dotenv\n"
        "DOKU_API_BASE=https://llm.example.com/api/v1\n"
        "DOKU_MODEL=openrouter:from-dotenv\n"
    )
    result = _run_cli_subprocess(tmp_path, extra_env={"DOKU_API_KEY": ""})
    assert result.returncode == 1
    assert "Missing required environment variable(s): DOKU_API_KEY." in result.stderr
    assert "No model configured" not in result.stderr
