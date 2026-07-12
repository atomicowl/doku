"""Builds the orchestrator deep agent that dynamically dispatches the
documenter subagents over every discovered candidate.

Agent definitions (system prompt + config.toml with name, description,
response_format, and filesystem permissions) live under `agents/`: the main
agent in `agents/orchestrator/`, one folder per subagent in
`agents/subagents/<name>/`. See `agents/README.md`.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from deepagents import FilesystemPermission, SubAgent, create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend
from deepagents.profiles.provider.provider_profiles import apply_provider_profile
from langchain.chat_models import init_chat_model
from langchain_quickjs import CodeInterpreterMiddleware

from doku import models

_AGENTS_DIR = Path(__file__).parent / "agents"

# Per-eval wall-clock budget. The default (5s) is sized for quick tool-assisted
# snippets, not a loop that dispatches many LLM subagent calls; give it room.
DEFAULT_INTERPRETER_TIMEOUT_SECONDS = 900.0


def _load_agent(agent_dir: Path) -> tuple[dict[str, Any], str]:
    config = tomllib.loads((agent_dir / "config.toml").read_text())
    prompt = (agent_dir / "prompt.md").read_text()
    return config, prompt


def _permissions(config: dict[str, Any], skill_sources: list[str]) -> list[FilesystemPermission]:
    """Config permissions, with the agent's skill mounts made readable and
    read-only up front (rules are first-match-wins, so these can't be undone
    by broad rules in the config).
    """
    permissions = [FilesystemPermission(**p) for p in config.get("permissions", [])]
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
    agent_name: str, agent_dir: Path, config: dict[str, Any]
) -> tuple[dict[str, FilesystemBackend], list[str]]:
    """Resolve the config's `skills` source dirs (relative to the agent's
    folder) into read-only backend mounts under /skills/<agent-name>/ plus the
    source paths to hand to deepagents' SkillsMiddleware.
    """
    routes: dict[str, FilesystemBackend] = {}
    sources: list[str] = []
    for rel in config.get("skills", []):
        host_dir = (agent_dir / rel).resolve()
        if not host_dir.is_dir():
            raise FileNotFoundError(
                f"agent '{agent_name}' declares skills dir {rel!r} but {host_dir} does not exist"
            )
        mount = f"/skills/{agent_name}/{host_dir.name}"
        routes[f"{mount}/"] = FilesystemBackend(root_dir=str(host_dir), virtual_mode=True)
        sources.append(mount)
    return routes, sources


def _load_subagents(agents_dir: Path) -> tuple[list[SubAgent], dict[str, FilesystemBackend]]:
    subagents: list[SubAgent] = []
    routes: dict[str, FilesystemBackend] = {}
    for agent_dir in sorted((agents_dir / "subagents").iterdir()):
        if not (agent_dir / "config.toml").is_file():
            continue
        config, prompt = _load_agent(agent_dir)
        skill_routes, skill_sources = _skill_mounts(config["name"], agent_dir, config)
        routes.update(skill_routes)
        subagent: SubAgent = {
            "name": config["name"],
            "description": config["description"],
            "system_prompt": prompt,
            "permissions": _permissions(config, skill_sources),
        }
        if skill_sources:
            subagent["skills"] = skill_sources
        if "response_format" in config:
            subagent["response_format"] = getattr(models, config["response_format"])
        subagents.append(subagent)
    return subagents, routes


def _resolve_model(model: str, api_key: str, api_base: str, chat_completions: bool = False):
    """Build the chat model with explicit credentials (no provider env-var
    fallbacks), keeping deepagents' provider-profile kwargs (e.g. OpenRouter
    app attribution) that a plain string model id would have received.

    `chat_completions` downgrades from the OpenAI Responses API (which the
    provider profile turns on for `openai:` models) to the plain Chat
    Completions API, for OpenAI-compatible servers (vLLM, Ollama, gateways)
    that don't implement the Responses API. No-op for providers whose profile
    doesn't involve the Responses API.
    """
    kwargs = {**apply_provider_profile(model), "api_key": api_key, "base_url": api_base}
    if chat_completions and "use_responses_api" in kwargs:
        kwargs["use_responses_api"] = False
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
    orchestrator_config, orchestrator_prompt = _load_agent(agents_dir / "orchestrator")
    orchestrator_prompt = orchestrator_prompt.replace("__CONCURRENCY__", str(concurrency))
    orchestrator_routes, orchestrator_skills = _skill_mounts(
        orchestrator_config["name"], agents_dir / "orchestrator", orchestrator_config
    )
    subagents, subagent_routes = _load_subagents(agents_dir)

    repo_backend = FilesystemBackend(root_dir=str(repo_path), virtual_mode=True)
    docs_backend = FilesystemBackend(root_dir=str(docs_dir), virtual_mode=True)
    backend = CompositeBackend(
        default=docs_backend,
        routes={"/repo/": repo_backend, **orchestrator_routes, **subagent_routes},
    )

    return create_deep_agent(
        model=_resolve_model(model, api_key, api_base, chat_completions),
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


def invoke_orchestrator(agent, num_candidates: int, display: Any | None = None):
    """Kick off the dispatch loop. Side effects land on disk under /_state;
    the returned value is the final graph state (same shape `agent.invoke`
    returns).

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
                    f"There are {num_candidates} entrypoint candidates in "
                    "/_state/entrypoints.json. Document every single one "
                    "as instructed."
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
