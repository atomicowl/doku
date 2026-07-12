"""Deterministic Markdown rendering.

The LLM subagents produce structured `EntrypointDoc` data; turning that into
the actual Markdown files is a pure formatting step and is kept out of agent
hands entirely, so output is consistent regardless of what the model does.
"""

from __future__ import annotations

from pathlib import Path

from doku.agent_config import resolve_response_model

EntrypointDoc = resolve_response_model(
    "models:EntrypointDoc",
    Path.cwd() / "agents/subagents/entrypoint_documenter",
)


def render_entrypoint_markdown(slug: str, doc: EntrypointDoc) -> str:
    lines = [
        f"# {doc.title}",
        "",
        f"- **Type:** {doc.type}",
        f"- **Location:** `{doc.location}`",
        "",
        "## Input",
        "",
        doc.input_model.strip(),
        "",
        "## Output",
        "",
        doc.output_model.strip(),
        "",
        "## Flow",
        "",
        "```mermaid",
        doc.flow_mermaid.strip(),
        "```",
        "",
        "## External dependencies",
        "",
    ]
    if doc.dependencies:
        lines += ["| Kind | Name | Usage |", "| --- | --- | --- |"]
        lines += [
            f"| {dep.kind} | {dep.name} | {dep.usage} |" for dep in doc.dependencies
        ]
    else:
        lines.append("_None found._")
    lines.append("")
    return "\n".join(lines)


def render_index(entries: list[tuple[str, EntrypointDoc]]) -> str:
    lines = [
        "# Entrypoints",
        "",
        "| Type | Title | Location | Doc |",
        "| --- | --- | --- | --- |",
    ]
    for slug, doc in sorted(entries, key=lambda e: (e[1].type, e[1].title)):
        lines.append(
            f"| {doc.type} | {doc.title} | `{doc.location}` | [{slug}](entrypoints/{slug}.md) |"
        )
    lines.append("")
    return "\n".join(lines)


def render_dependencies(entries: list[tuple[str, EntrypointDoc]]) -> str:
    # (kind, name) -> list of (slug, title, usage)
    by_dependency: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    for slug, doc in entries:
        for dep in doc.dependencies:
            by_dependency.setdefault((dep.kind, dep.name), []).append(
                (slug, doc.title, dep.usage)
            )

    lines = ["# External dependencies", ""]
    if not by_dependency:
        lines.append("_None found._")
        lines.append("")
        return "\n".join(lines)

    for (kind, name) in sorted(by_dependency):
        lines.append(f"## {name} ({kind})")
        lines.append("")
        lines.append("| Used by | Usage |")
        lines.append("| --- | --- |")
        for slug, title, usage in by_dependency[(kind, name)]:
            lines.append(f"| [{title}](entrypoints/{slug}.md) | {usage} |")
        lines.append("")
    return "\n".join(lines)


def render_errors(errors: list[dict[str, str]]) -> str:
    lines = ["# Entrypoints that failed to document", ""]
    for err in errors:
        lines.append(f"- `{err.get('slug', '?')}`: {err.get('error', 'unknown error')}")
    lines.append("")
    return "\n".join(lines)
