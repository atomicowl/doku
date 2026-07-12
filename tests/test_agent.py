"""Agent-config loading: per-agent folders, permissions, and skills wiring."""

from pathlib import Path

import pytest
from deepagents.backends import CompositeBackend, FilesystemBackend
from deepagents.middleware.skills import _list_skills

from doku.agent import _load_agent, _load_subagents, _permissions, _skill_mounts, build_orchestrator

SKILL_MD = """\
---
name: flow-graphs
description: How to draw good mermaid flow graphs.
---

# Flow graphs

Keep node labels short.
"""


def make_agents_dir(tmp_path: Path, *, with_skills: bool) -> Path:
    agents = tmp_path / "agents"
    orch = agents / "orchestrator"
    orch.mkdir(parents=True)
    orch_config = 'name = "orchestrator"\n'
    if with_skills:
        orch_config += 'skills = ["skills"]\n'
        (orch / "skills" / "flow-graphs").mkdir(parents=True)
        (orch / "skills" / "flow-graphs" / "SKILL.md").write_text(SKILL_MD)
    (orch / "config.toml").write_text(orch_config)
    (orch / "prompt.md").write_text("Dispatch with concurrency __CONCURRENCY__.")

    doc = agents / "subagents" / "documenter"
    doc.mkdir(parents=True)
    (doc / "config.toml").write_text(
        'name = "documenter"\n'
        'description = "Documents one entrypoint."\n'
        'response_format = "EntrypointDoc"\n'
        + ('skills = ["skills"]\n' if with_skills else "")
        + '[[permissions]]\n'
        'operations = ["read", "write"]\n'
        'paths = ["/**"]\n'
        'mode = "deny"\n'
    )
    (doc / "prompt.md").write_text("Document the entrypoint.")
    if with_skills:
        (doc / "skills" / "flow-graphs").mkdir(parents=True)
        (doc / "skills" / "flow-graphs" / "SKILL.md").write_text(SKILL_MD)
    return agents


def test_load_subagents_without_skills(tmp_path):
    agents_dir = make_agents_dir(tmp_path, with_skills=False)
    subagents, routes = _load_subagents(agents_dir)
    assert routes == {}
    (subagent,) = subagents
    assert subagent["name"] == "documenter"
    assert "skills" not in subagent
    assert subagent["response_format"].__name__ == "EntrypointDoc"
    (deny,) = subagent["permissions"]
    assert deny.mode == "deny"


def test_load_subagents_with_skills(tmp_path):
    agents_dir = make_agents_dir(tmp_path, with_skills=True)
    subagents, routes = _load_subagents(agents_dir)
    (subagent,) = subagents
    assert subagent["skills"] == ["/skills/documenter/skills"]
    assert list(routes) == ["/skills/documenter/skills/"]
    # Auto-granted read (and deny-write) on the mount come first, so they win
    # over the config's own deny-everything rule.
    allow_read, deny_write, config_deny = subagent["permissions"]
    assert allow_read.mode == "allow" and allow_read.operations == ["read"]
    assert "/skills/documenter/skills" in allow_read.paths
    assert "/skills/documenter/skills/**" in allow_read.paths
    assert deny_write.mode == "deny" and deny_write.operations == ["write"]
    assert config_deny.paths == ["/**"]


def test_skill_mounts_missing_dir_raises(tmp_path):
    agents_dir = make_agents_dir(tmp_path, with_skills=False)
    orch_dir = agents_dir / "orchestrator"
    config, _prompt = _load_agent(orch_dir)
    config["skills"] = ["skills"]
    with pytest.raises(FileNotFoundError, match="orchestrator"):
        _skill_mounts("orchestrator", orch_dir, config)


def test_no_skill_permissions_injected_without_sources(tmp_path):
    agents_dir = make_agents_dir(tmp_path, with_skills=False)
    config, _prompt = _load_agent(agents_dir / "subagents" / "documenter")
    assert len(_permissions(config, [])) == 1


def test_skills_discoverable_through_composite_backend(tmp_path):
    """The mounted source must be listable/readable the way SkillsMiddleware
    does it (backend ls + download of each <skill>/SKILL.md)."""
    agents_dir = make_agents_dir(tmp_path, with_skills=True)
    _subagents, routes = _load_subagents(agents_dir)
    backend = CompositeBackend(
        default=FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True),
        routes=routes,
    )
    (skill,) = _list_skills(backend, "/skills/documenter/skills")
    assert skill["name"] == "flow-graphs"
    assert skill["description"] == "How to draw good mermaid flow graphs."


def test_build_orchestrator_with_skills(tmp_path):
    agents_dir = make_agents_dir(tmp_path, with_skills=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    agent = build_orchestrator(
        repo_path=repo,
        docs_dir=tmp_path / "docs",
        model="openrouter:z-ai/glm-5.2",
        api_key="test-key",
        api_base="https://llm.example.com/api/v1",
        concurrency=3,
        agents_dir=agents_dir,
    )
    assert agent is not None
