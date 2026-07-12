from pathlib import Path

import pytest

from doku.workflow import discover_named, load_workflow


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
