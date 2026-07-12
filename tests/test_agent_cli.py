from typer.testing import CliRunner

from doku.agent_cli import app


runner = CliRunner()


def test_create_text_agent(tmp_path):
    result = runner.invoke(
        app, ["create", "reviewer", "--output", "text", "--agents-dir", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert 'output = "text"' in (tmp_path / "subagents/reviewer/config.toml").read_text()


def test_create_structured_agent_with_local_model(tmp_path):
    result = runner.invoke(
        app,
        [
            "create", "reviewer", "--output", "structured", "--model", "models:Review",
            "--agents-dir", str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert "class Review(BaseModel)" in (tmp_path / "subagents/reviewer/models.py").read_text()
