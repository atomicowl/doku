"""Builds the orchestrator deep agent that dynamically dispatches the
documenter subagents over every discovered candidate.

Agent definitions (system prompt + config.toml with name, description,
response_format, and filesystem permissions) live under `agents/`: the main
agent in `agents/main/`, one folder per subagent in
`agents/subagents/<name>/`. See `agents/README.md`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from deepagents import FilesystemPermission, SubAgent, create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend
from deepagents.profiles.provider.provider_profiles import apply_provider_profile
from langchain.chat_models import init_chat_model
from langchain_core.rate_limiters import InMemoryRateLimiter
from langchain_quickjs import CodeInterpreterMiddleware

from doku.agent_config import AgentConfig, resolve_response_model
from doku.workflow import LoadedWorkflow

_AGENTS_DIR = Path.cwd() / "agents"

# Per-eval wall-clock budget. The default (5s) is sized for quick tool-assisted
# snippets, not a loop that dispatches many LLM subagent calls; give it room.
DEFAULT_INTERPRETER_TIMEOUT_SECONDS = 900.0


def _compose_workflow_prompt(
    workflow: LoadedWorkflow, agents_dir: Path, *, concurrency: int
) -> str:
    """Prepend optional global main-agent instructions to workflow instructions."""
    workflow_prompt = workflow.prompt(CONCURRENCY=concurrency)
    main_prompt_path = agents_dir / "main" / "prompt.md"
    main_prompt = main_prompt_path.read_text().strip() if main_prompt_path.is_file() else ""
    return f"{main_prompt}\n\n{workflow_prompt}" if main_prompt else workflow_prompt


def _load_agent(agent_dir: Path) -> tuple[AgentConfig, str]:
    import tomllib

    config = AgentConfig.model_validate(tomllib.loads((agent_dir / "config.toml").read_text()))
    prompt = (agent_dir / "prompt.md").read_text()
    return config, prompt


def _permissions(config: AgentConfig, skill_sources: list[str]) -> list[FilesystemPermission]:
    """Config permissions, with the agent's skill mounts made readable and
    read-only up front (rules are first-match-wins, so these can't be undone
    by broad rules in the config).
    """
    permissions = [FilesystemPermission(**p.model_dump()) for p in config.permissions]
    if skill_sources:
        # Mount points listed alongside the "/**" globs for the same reason
        # as "/repo" below: the glob doesn't match the mount point itself.
        skill_paths = [p for source in skill_sources for p in (source, f"{source}/**")]
        permissions = [
            FilesystemPermission(operations=["read"], paths=skill_paths, mode="allow"),
            FilesystemPermission(operations=["write"], paths=skill_paths, mode="deny"),
            *permissions,
        ]
    return permissions


def _skill_mounts(
    agent_name: str, agent_dir: Path, config: AgentConfig
) -> tuple[dict[str, FilesystemBackend], list[str]]:
    """Resolve the config's `skills` source dirs (relative to the agent's
    folder) into read-only backend mounts under /skills/<agent-name>/ plus the
    source paths to hand to deepagents' SkillsMiddleware.
    """
    routes: dict[str, FilesystemBackend] = {}
    sources: list[str] = []
    for rel in config.skills:
        host_dir = (agent_dir / rel).resolve()
        if not host_dir.is_dir():
            raise FileNotFoundError(
                f"agent '{agent_name}' declares skills dir {rel!r} but {host_dir} does not exist"
            )
        mount = f"/skills/{agent_name}/{host_dir.name}"
        routes[f"{mount}/"] = FilesystemBackend(root_dir=str(host_dir), virtual_mode=True)
        sources.append(mount)
    return routes, sources


def _load_subagents(
    agents_dir: Path, selected_names: set[str] | None = None,
) -> tuple[list[SubAgent], dict[str, FilesystemBackend], dict[str, str]]:
    """Load every subagent folder; returns (subagents, skill routes, roles).

    `roles` maps subagent name -> its config's `role` ("discoverer" agents
    are dispatched in phase 1, the "documenter" in phase 2 — see
    `_fill_orchestrator_prompt`), so dropping a new folder in is all it takes
    to extend a run.
    """
    subagents: list[SubAgent] = []
    routes: dict[str, FilesystemBackend] = {}
    roles: dict[str, str] = {}
    for agent_dir in sorted((agents_dir / "subagents").iterdir()):
        if not (agent_dir / "config.toml").is_file():
            continue
        if selected_names is not None and not (
            agent_dir.name in selected_names
            or agent_dir.name.replace("_", "-") in selected_names
        ):
            continue
        config, prompt = _load_agent(agent_dir)
        if selected_names is not None and config.name not in selected_names:
            continue
        role = config.role
        if role:
            roles[config.name] = role
        skill_routes, skill_sources = _skill_mounts(config.name, agent_dir, config)
        routes.update(skill_routes)
        subagent: SubAgent = {
            "name": config.name,
            "description": config.description or "",
            "system_prompt": prompt,
            "permissions": _permissions(config, skill_sources),
        }
        if skill_sources:
            subagent["skills"] = skill_sources
        if config.output == "structured":
            subagent["response_format"] = resolve_response_model(config.model_reference, agent_dir)
        subagents.append(subagent)
    return subagents, routes, roles


def _fill_orchestrator_prompt(
    template: str, *, concurrency: int, discoverers: list[SubAgent], documenter: str
) -> str:
    """Resolve the orchestrator template against the subagents on disk, so
    the run flow is derived from the folders rather than hardcoded."""
    bullets = "\n".join(
        f"- `{d['name']}` — {' '.join(d['description'].split())}" for d in discoverers
    )
    dispatch_list = json.dumps(
        [
            {"subagentType": d["name"], "label": f"discover-{d['name']}"}
            for d in discoverers
        ],
        indent=2,
    )
    return (
        template.replace("__CONCURRENCY__", str(concurrency))
        .replace("__DISCOVERERS_LIST__", bullets)
        .replace("__DISCOVERERS_JS__", dispatch_list)
        .replace("__DOCUMENTER__", documenter)
    )


def _resolve_model(
    model: str,
    api_key: str,
    api_base: str,
    chat_completions: bool = False,
    model_rps: float | None = None,
    model_burst: int = 1,
    max_retries: int | None = None,
    temperature: float | None = None,
    reasoning_effort: str | None = None,
):
    """Build the chat model with explicit credentials (no provider env-var
    fallbacks), keeping deepagents' provider-profile kwargs (e.g. OpenRouter
    app attribution) that a plain string model id would have received.

    `chat_completions` downgrades from the OpenAI Responses API (which the
    provider profile turns on for `openai:` models) to the plain Chat
    Completions API, for OpenAI-compatible servers (vLLM, Ollama, gateways)
    that don't implement the Responses API. No-op for providers whose profile
    doesn't involve the Responses API.

    `model_rps` attaches an `InMemoryRateLimiter` capping request starts at
    that many per second, with bursts of up to `model_burst`. The orchestrator
    and every subagent share this one model instance, so the cap is global
    across the whole run regardless of `--concurrency`.

    `max_retries` caps the provider client's retries per request (0 disables
    retrying); unset keeps the provider's own default.
    """
    kwargs = {**apply_provider_profile(model), "api_key": api_key, "base_url": api_base}
    if chat_completions and "use_responses_api" in kwargs:
        kwargs["use_responses_api"] = False
    if model_rps is not None:
        kwargs["rate_limiter"] = InMemoryRateLimiter(
            requests_per_second=model_rps, max_bucket_size=model_burst
        )
    if max_retries is not None:
        kwargs["max_retries"] = max_retries
    if temperature is not None:
        kwargs["temperature"] = temperature
    if reasoning_effort is not None:
        if model.startswith("openrouter:"):
            kwargs["reasoning"] = {"effort": reasoning_effort}
        else:
            kwargs["reasoning_effort"] = reasoning_effort
    return init_chat_model(model, **kwargs)


def build_orchestrator(
    *,
    repo_path: Path,
    docs_dir: Path,
    model: str,
    api_key: str,
    api_base: str,
    concurrency: int,
    chat_completions: bool = False,
    model_rps: float | None = None,
    model_burst: int = 1,
    max_retries: int | None = None,
    temperature: float | None = None,
    reasoning_effort: str | None = None,
    interpreter_timeout: float = DEFAULT_INTERPRETER_TIMEOUT_SECONDS,
    agents_dir: Path = _AGENTS_DIR,
):
    """Construct the orchestrator deep agent.

    `/repo` is mounted read-only from `repo_path`; everything else (the
    default route) is read-write, backed by `docs_dir`, for the orchestrator's
    dispatch loop to persist raw per-entrypoint results to
    `/_state/results/*.json`. The `entrypoint-documenter` subagent only ever
    sees `/repo`, read-only.

    Agents that declare `skills` in their config.toml additionally get their
    skill source dirs mounted read-only under /skills/<agent-name>/.
    """
    orchestrator_config, orchestrator_template = _load_agent(agents_dir / "main")
    orchestrator_routes, orchestrator_skills = _skill_mounts(
        orchestrator_config.name, agents_dir / "main", orchestrator_config
    )
    subagents, subagent_routes, roles = _load_subagents(agents_dir)

    discoverers = [s for s in subagents if roles[s["name"]] == "discoverer"]
    documenters = [s["name"] for s in subagents if roles[s["name"]] == "documenter"]
    if not discoverers:
        raise ValueError(f"no subagent with role = \"discoverer\" found under {agents_dir}")
    if len(documenters) != 1:
        raise ValueError(
            f"exactly one subagent with role = \"documenter\" is required, "
            f"found {len(documenters)}: {documenters}"
        )
    orchestrator_prompt = _fill_orchestrator_prompt(
        orchestrator_template,
        concurrency=concurrency,
        discoverers=discoverers,
        documenter=documenters[0],
    )

    repo_backend = FilesystemBackend(root_dir=str(repo_path), virtual_mode=True)
    docs_backend = FilesystemBackend(root_dir=str(docs_dir), virtual_mode=True)
    backend = CompositeBackend(
        default=docs_backend,
        routes={"/repo/": repo_backend, **orchestrator_routes, **subagent_routes},
    )

    return create_deep_agent(
        model=_resolve_model(
            model, api_key, api_base, chat_completions, model_rps, model_burst,
            max_retries, temperature, reasoning_effort
        ),
        system_prompt=orchestrator_prompt,
        subagents=subagents,
        skills=orchestrator_skills or None,
        middleware=[
            CodeInterpreterMiddleware(
                timeout=interpreter_timeout,
                ptc=["read_file", "write_file"],
            )
        ],
        backend=backend,
        permissions=_permissions(orchestrator_config, orchestrator_skills),
    )


def build_workflow_agent(
    *,
    workflow: LoadedWorkflow,
    repo_path: Path,
    output_dir: Path,
    agents_dir: Path,
    model: str,
    api_key: str,
    api_base: str,
    concurrency: int,
    chat_completions: bool = False,
    model_rps: float | None = None,
    model_burst: int = 1,
    max_retries: int | None = None,
    temperature: float | None = None,
    reasoning_effort: str | None = None,
    interpreter_timeout: float = DEFAULT_INTERPRETER_TIMEOUT_SECONDS,
):
    """Build a task-agnostic workflow orchestrator.

    The workflow owns its prompt, output contract, and exact subagent
    allowlist. The harness only supplies execution, model, and filesystem
    facilities.
    """
    requested = set(workflow.config.subagents)
    available, routes, _roles = _load_subagents(agents_dir, selected_names=requested)
    by_name = {agent["name"]: agent for agent in available}
    missing = [name for name in workflow.config.subagents if name not in by_name]
    if missing:
        raise ValueError(
            f"workflow '{workflow.config.name}' references missing subagent(s): "
            f"{', '.join(missing)}"
        )
    selected = [by_name[name] for name in workflow.config.subagents]

    repo_backend = FilesystemBackend(root_dir=str(repo_path), virtual_mode=True)
    output_backend = FilesystemBackend(root_dir=str(output_dir), virtual_mode=True)
    backend = CompositeBackend(
        default=output_backend,
        routes={"/repo/": repo_backend, **routes},
    )
    permissions = [
        FilesystemPermission(operations=["write"], paths=["/repo/**"], mode="deny")
    ]
    kwargs: dict[str, Any] = {}
    if workflow.response_model is not None:
        kwargs["response_format"] = workflow.response_model
    system_prompt = _compose_workflow_prompt(
        workflow, agents_dir, concurrency=concurrency
    )
    return create_deep_agent(
        model=_resolve_model(
            model, api_key, api_base, chat_completions, model_rps, model_burst,
            max_retries, temperature, reasoning_effort
        ),
        system_prompt=system_prompt,
        subagents=selected,
        middleware=[
            CodeInterpreterMiddleware(
                timeout=interpreter_timeout,
                ptc=["read_file", "write_file"],
            )
        ],
        backend=backend,
        permissions=permissions,
        **kwargs,
    )


def invoke_orchestrator(agent, display: Any | None = None, request: str | None = None):
    """Kick off the run: agentic discovery, then the documentation dispatch
    loop. Side effects land on disk under /_state; the returned value is the
    final graph state (same shape `agent.invoke` returns).

    The whole dispatch loop otherwise runs inside a single `eval` tool call
    that doesn't return until every batch is done, so without `display`
    (duck-typed to `doku.progress.RunDisplay`) there is no visibility into
    what's happening for however long that takes. Streaming with
    `subgraphs=True` yields `(namespace, mode, chunk)` triples; namespace
    `()` is the orchestrator itself, child namespaces are the subagent runs
    happening *inside* the `eval` call:

    - `custom` chunks -> `display.on_subagent_event`: each `task()`
      dispatch's start/complete/error, emitted by `langchain_quickjs` via
      `runtime.stream_writer`.
    - `updates` chunks -> `display.on_update(namespace, node, update)`: the
      orchestrator's reasoning/tool calls/results, and — via the child
      namespaces — every subagent's own turns and tool calls.
    - child `values` chunks -> `display.on_values(namespace, state)`: lets
      the display correlate a child namespace to its dispatch (and recover
      the full, untruncated prompt) by matching `messages[0]` against the
      start event's `description` prefix.
    """
    input_ = {
        "messages": [
            {
                "role": "user",
                "content": (
                    request
                    or "Run the configured workflow over the codebase mounted at /repo."
                ),
            }
        ]
    }
    if display is None:
        return agent.invoke(input_)

    final_state = None
    for namespace, mode, chunk in agent.stream(
        input_, stream_mode=["values", "custom", "updates"], subgraphs=True
    ):
        if mode == "custom":
            display.on_subagent_event(chunk)
        elif mode == "updates":
            if isinstance(chunk, dict):
                for node_name, node_update in chunk.items():
                    display.on_update(namespace, node_name, node_update)
        elif mode == "values":
            if namespace:
                display.on_values(namespace, chunk)
            else:
                final_state = chunk
    return final_state
