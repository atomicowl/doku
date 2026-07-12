from pathlib import Path

import pytest

from doku.workflow import discover_named, load_workflow
from doku.agent import _compose_workflow_prompt


def test_load_text_workflow_and_subagent_allowlist(tmp_path):
    (tmp_path / "prompt.md").write_text("Batch __CONCURRENCY__")
    (tmp_path / "config.toml").write_text(
        'name = "review"\nsubagents = ["reviewer"]\noutput = "text"\n'
    )
    workflow = load_workflow(tmp_path)
    assert workflow.config.subagents == ["reviewer"]
    assert workflow.prompt(CONCURRENCY=3) == "Batch 3"


def test_structured_workflow_loads_local_model(tmp_path):
    (tmp_path / "prompt.md").write_text("Review")
    (tmp_path / "models.py").write_text(
        "from pydantic import BaseModel\nclass Result(BaseModel):\n    summary: str\n"
    )
    (tmp_path / "config.toml").write_text(
        'name = "review"\nsubagents = []\noutput = "structured"\n'
        'response_model = "models:Result"\n'
    )
    workflow = load_workflow(tmp_path)
    assert workflow.response_model(summary="ok").summary == "ok"


def test_structured_workflow_requires_model(tmp_path):
    (tmp_path / "prompt.md").write_text("Review")
    (tmp_path / "config.toml").write_text(
        'name = "review"\nsubagents = []\noutput = "structured"\n'
    )
    with pytest.raises(Exception, match="require response_model"):
        load_workflow(tmp_path)


def test_discover_named_accepts_name_or_path(tmp_path):
    bundled = tmp_path / "my_workflow"
    bundled.mkdir()
    (bundled / "config.toml").write_text("")
    assert discover_named(tmp_path, "my-workflow") == bundled.resolve()
    assert discover_named(tmp_path / "unused", str(bundled)) == bundled.resolve()


def test_discover_named_prefers_configured_root_for_names(tmp_path, monkeypatch):
    root = tmp_path / "definitions"
    named = root / "security-review"
    named.mkdir(parents=True)
    (named / "config.toml").write_text("")
    monkeypatch.chdir(tmp_path)

    assert discover_named(root, "security-review") == named.resolve()


def test_global_main_prompt_is_prepended_to_workflow_prompt(tmp_path):
    workflow_dir = tmp_path / "workflow"
    workflow_dir.mkdir()
    (workflow_dir / "prompt.md").write_text("Run with __CONCURRENCY__ workers.")
    (workflow_dir / "config.toml").write_text(
        'name = "review"\nsubagents = []\noutput = "text"\n'
    )
    agents_dir = tmp_path / "agents"
    (agents_dir / "main").mkdir(parents=True)
    (agents_dir / "main" / "prompt.md").write_text("Follow company policy.\n")

    prompt = _compose_workflow_prompt(
        load_workflow(workflow_dir), agents_dir, concurrency=4
    )

    assert prompt == "Follow company policy.\n\nRun with 4 workers."


def test_bundled_documentation_workflow_includes_specialists():
    root = Path(__file__).parents[1]
    workflow = load_workflow(root / "workflows/document_codebase")
    assert {
        "decision-flow-analyzer",
        "feature-toggle-analyzer",
        "external-dependency-analyzer",
        "entrypoint-documenter",
    } <= set(workflow.config.subagents)
    prompt = workflow.prompt(CONCURRENCY=2)
    assert 'type: "decision-flow-analyzer"' in prompt
    assert 'type: "feature-toggle-analyzer"' in prompt
    assert 'type: "external-dependency-analyzer"' in prompt
