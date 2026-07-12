"""Agent-config loading: per-agent folders, permissions, and skills wiring."""

from pathlib import Path

import pytest
from deepagents.backends import CompositeBackend, FilesystemBackend
from deepagents.middleware.skills import _list_skills

from doku.agent import _AGENTS_DIR
from doku.agent import (
    _fill_orchestrator_prompt,
    _load_agent,
    _load_subagents,
    _permissions,
    _resolve_model,
    _skill_mounts,
    build_orchestrator,
)
from doku.agent_config import resolve_response_model

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
    orch = agents / "main"
    orch.mkdir(parents=True)
    orch_config = 'name = "main"\n'
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
        'role = "documenter"\n'
        'description = "Documents one item."\n'
        'output = "structured"\n'
        f'response_model = "{(_AGENTS_DIR / "subagents/entrypoint_documenter/models.py")}:EntrypointDoc"\n'
        + ('skills = ["skills"]\n' if with_skills else "")
        + '[[permissions]]\n'
        'operations = ["read", "write"]\n'
        'paths = ["/**"]\n'
        'mode = "deny"\n'
    )
    (doc / "prompt.md").write_text("Document the item.")
    if with_skills:
        (doc / "skills" / "flow-graphs").mkdir(parents=True)
        (doc / "skills" / "flow-graphs" / "SKILL.md").write_text(SKILL_MD)

    disc = agents / "subagents" / "finder"
    disc.mkdir(parents=True)
    (disc / "models.py").write_text(
        "from pydantic import BaseModel, Field\n"
        "class FoundItems(BaseModel):\n"
        "    items: list[dict] = Field(default_factory=list)\n"
    )
    (disc / "config.toml").write_text(
        'name = "finder"\n'
        'role = "discoverer"\n'
        'description = "Finds items."\n'
        'output = "structured"\n'
        'response_model = "models:FoundItems"\n'
    )
    (disc / "prompt.md").write_text("Find the items.")
    return agents


def test_shipped_agents_include_discoverers_and_documenter():
    subagents, _routes, roles = _load_subagents(_AGENTS_DIR)
    by_name = {s["name"]: s for s in subagents}
    assert set(by_name) == {
        "decision-flow-analyzer",
        "entrypoint-documenter",
        "external-dependency-analyzer",
        "feature-toggle-analyzer",
        "kafka-consumer-extractor",
        "rest-api-extractor",
        "soap-api-extractor",
    }
    for name in ("rest-api-extractor", "soap-api-extractor", "kafka-consumer-extractor"):
        assert roles[name] == "discoverer"
    assert by_name["rest-api-extractor"]["response_format"].__name__ == "RestEndpoints"
    assert by_name["soap-api-extractor"]["response_format"].__name__ == "SoapOperations"
    assert by_name["kafka-consumer-extractor"]["response_format"].__name__ == "KafkaConsumers"
    assert by_name["decision-flow-analyzer"]["response_format"].__name__ == "DecisionFlowAnalysis"
    assert by_name["feature-toggle-analyzer"]["response_format"].__name__ == "FeatureToggleAnalysis"
    assert by_name["external-dependency-analyzer"]["response_format"].__name__ == "ExternalDependencyAnalysis"
    assert roles["entrypoint-documenter"] == "documenter"
    assert by_name["entrypoint-documenter"]["response_format"].__name__ == "EntrypointDoc"


def test_load_subagents_without_skills(tmp_path):
    agents_dir = make_agents_dir(tmp_path, with_skills=False)
    subagents, routes, roles = _load_subagents(agents_dir)
    assert routes == {}
    assert roles == {"documenter": "documenter", "finder": "discoverer"}
    subagent = next(s for s in subagents if s["name"] == "documenter")
    assert "skills" not in subagent
    assert subagent["response_format"].__name__ == "EntrypointDoc"
    (deny,) = subagent["permissions"]
    assert deny.mode == "deny"


def test_load_subagents_with_skills(tmp_path):
    agents_dir = make_agents_dir(tmp_path, with_skills=True)
    subagents, routes, _roles = _load_subagents(agents_dir)
    subagent = next(s for s in subagents if s["name"] == "documenter")
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
    orch_dir = agents_dir / "main"
    config, _prompt = _load_agent(orch_dir)
    config["skills"] = ["skills"]
    with pytest.raises(FileNotFoundError, match="main"):
        _skill_mounts("main", orch_dir, config)


def test_no_skill_permissions_injected_without_sources(tmp_path):
    agents_dir = make_agents_dir(tmp_path, with_skills=False)
    config, _prompt = _load_agent(agents_dir / "subagents" / "documenter")
    assert len(_permissions(config, [])) == 1


def test_skills_discoverable_through_composite_backend(tmp_path):
    """The mounted source must be listable/readable the way SkillsMiddleware
    does it (backend ls + download of each <skill>/SKILL.md)."""
    agents_dir = make_agents_dir(tmp_path, with_skills=True)
    _subagents, routes, _roles = _load_subagents(agents_dir)
    backend = CompositeBackend(
        default=FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True),
        routes=routes,
    )
    (skill,) = _list_skills(backend, "/skills/documenter/skills")
    assert skill["name"] == "flow-graphs"
    assert skill["description"] == "How to draw good mermaid flow graphs."


def test_openai_models_default_to_responses_api():
    model = _resolve_model("openai:gpt-5.2", "test-key", "https://llm.example.com/v1")
    assert model.use_responses_api is True


def test_chat_completions_overrides_responses_api():
    model = _resolve_model(
        "openai:llama-3.3-70b", "test-key", "http://localhost:8000/v1", chat_completions=True
    )
    assert model.use_responses_api is False


def test_chat_completions_is_noop_for_other_providers():
    model = _resolve_model(
        "openrouter:z-ai/glm-5.2", "test-key", "https://llm.example.com/api/v1", chat_completions=True
    )
    assert type(model).__name__ == "ChatOpenRouter"


def test_rate_limiter_attached_when_rps_set():
    model = _resolve_model(
        "openrouter:z-ai/glm-5.2", "k", "https://x/api/v1", model_rps=2.0, model_burst=5
    )
    assert model.rate_limiter is not None
    assert model.rate_limiter.requests_per_second == 2.0
    assert model.rate_limiter.max_bucket_size == 5


def test_no_rate_limiter_by_default():
    model = _resolve_model("openrouter:z-ai/glm-5.2", "k", "https://x/api/v1")
    assert model.rate_limiter is None


def test_max_retries_set_when_configured():
    model = _resolve_model("openrouter:z-ai/glm-5.2", "k", "https://x/api/v1", max_retries=0)
    assert model.max_retries == 0


def test_max_retries_keeps_provider_default_when_unset():
    model = _resolve_model("openrouter:z-ai/glm-5.2", "k", "https://x/api/v1")
    assert model.max_retries == 2  # ChatOpenRouter's own default


def test_fill_orchestrator_prompt_resolves_all_placeholders():
    template = (
        "batch __CONCURRENCY__\n__DISCOVERERS_LIST__\n"
        "const discoverers = __DISCOVERERS_JS__;\nsubagentType: \"__DOCUMENTER__\""
    )
    discoverers = [
        {"name": "finder-a", "description": "Finds  A\nitems."},
        {"name": "finder-b", "description": "Finds B items."},
    ]
    filled = _fill_orchestrator_prompt(
        template, concurrency=4, discoverers=discoverers, documenter="documenter"
    )
    assert "batch 4" in filled
    assert "- `finder-a` — Finds A items." in filled  # whitespace normalized
    assert '"subagentType": "finder-a"' in filled
    assert '"label": "discover-finder-b"' in filled
    assert 'subagentType: "documenter"' in filled
    assert "__" not in filled  # no placeholder left behind


def test_role_is_optional_for_workflow_selected_agents(tmp_path):
    agents_dir = make_agents_dir(tmp_path, with_skills=False)
    config_path = agents_dir / "subagents" / "finder" / "config.toml"
    config_path.write_text(config_path.read_text().replace('role = "discoverer"\n', ""))
    subagents, _routes, roles = _load_subagents(agents_dir)
    assert "finder" in {agent["name"] for agent in subagents}
    assert "finder" not in roles


def test_build_requires_a_discoverer_and_one_documenter(tmp_path):
    agents_dir = make_agents_dir(tmp_path, with_skills=False)
    build = lambda: build_orchestrator(  # noqa: E731
        repo_path=tmp_path,
        docs_dir=tmp_path / "docs",
        model="openrouter:m",
        api_key="k",
        api_base="https://x/v1",
        concurrency=2,
        agents_dir=agents_dir,
    )

    config_path = agents_dir / "subagents" / "finder" / "config.toml"
    original = config_path.read_text()
    config_path.write_text(original.replace('role = "discoverer"', 'role = "documenter"'))
    with pytest.raises(ValueError, match="no subagent with role"):
        build()

    config_path.write_text(original)
    doc_config = agents_dir / "subagents" / "documenter" / "config.toml"
    doc_config.write_text(
        doc_config.read_text().replace('role = "documenter"', 'role = "discoverer"')
    )
    with pytest.raises(ValueError, match="exactly one subagent"):
        build()


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


def test_custom_text_agent_loads_without_response_format(tmp_path):
    agents_dir = make_agents_dir(tmp_path, with_skills=False)
    custom = agents_dir / "subagents" / "reviewer"
    custom.mkdir()
    (custom / "config.toml").write_text(
        'name = "reviewer"\nrole = "reviewer"\ndescription = "Reviews code."\noutput = "text"\n'
    )
    (custom / "prompt.md").write_text("Review the code.")
    subagents, _routes, roles = _load_subagents(agents_dir)
    reviewer = next(agent for agent in subagents if agent["name"] == "reviewer")
    assert "response_format" not in reviewer
    assert roles["reviewer"] == "reviewer"


def test_agent_local_response_model(tmp_path):
    agent_dir = tmp_path / "reviewer"
    agent_dir.mkdir()
    (agent_dir / "models.py").write_text(
        "from pydantic import BaseModel\nclass Review(BaseModel):\n    summary: str\n"
    )
    model = resolve_response_model("models:Review", agent_dir)
    assert model(summary="ok").summary == "ok"
