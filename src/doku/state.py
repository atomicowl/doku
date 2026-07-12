"""On-disk pipeline state and final output generation.

Everything the run persists lives under the docs output directory:
`_state/` is the contract with the orchestrator agent (which writes the
discovered-candidate manifest and the raw per-entrypoint results), and the
rendered Markdown next to it is the user-facing product. This module owns
that layout and every read/write against it, so the CLI stays argument
parsing and wiring.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from doku.models import EntrypointDoc
from doku.progress import text_of
from doku.render import (
    render_dependencies,
    render_entrypoint_markdown,
    render_errors,
    render_index,
)


@dataclass(frozen=True)
class StateLayout:
    """The on-disk layout under the docs output directory.

    Single source of truth for the paths shared between the CLI and the
    orchestrator, which addresses the same files as `/_state/...` inside its
    virtual filesystem (see the orchestrator prompt).
    """

    out: Path

    @property
    def state_dir(self) -> Path:
        return self.out / "_state"

    @property
    def results_dir(self) -> Path:
        return self.state_dir / "results"

    @property
    def entrypoints_dir(self) -> Path:
        return self.out / "entrypoints"

    @property
    def entrypoints_json(self) -> Path:
        return self.state_dir / "entrypoints.json"

    @property
    def errors_json(self) -> Path:
        return self.state_dir / "errors.json"

    @property
    def log_path(self) -> Path:
        return self.state_dir / "run.log"

    def ensure_dirs(self) -> None:
        for directory in (self.entrypoints_dir, self.results_dir):
            directory.mkdir(parents=True, exist_ok=True)


def read_manifest(layout: StateLayout) -> list[dict]:
    """Read the candidate manifest the orchestrator wrote after discovery.

    The manifest is model-authored (the orchestrator's dispatch loop writes
    it from the discovery subagents' merged output), so be tolerant: a
    missing or unparsable file yields `[]`, entries are only required to be
    objects, and a missing `slug` is derived from the candidate fields the
    same way the orchestrator is instructed to build it.
    """
    if not layout.entrypoints_json.exists():
        return []
    try:
        raw = json.loads(layout.entrypoints_json.read_text())
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    entries = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if not item.get("slug"):
            derived = "-".join(
                str(item.get(key, "unknown"))
                for key in ("type", "class_name", "method_name")
            ).lower()
            item["slug"] = "".join(
                ch if ch.isalnum() or ch in "_-" else "-" for ch in derived
            )
        entries.append(item)
    return entries


def final_message_text(result) -> str | None:
    """The orchestrator's final summary message, if it produced one."""
    messages = result.get("messages") if isinstance(result, dict) else None
    if not messages:
        return None
    last = messages[-1]
    content = getattr(last, "content", None) if not isinstance(last, dict) else last.get("content")
    return text_of(content) or None


_ENTRYPOINT_DOC_FIELDS = set(EntrypointDoc.model_fields)


def _unwrap_entrypoint_doc(value, depth: int = 0):
    """Find the EntrypointDoc-shaped payload inside model-authored JSON.

    The orchestrator's dispatch loop is LLM-authored JS, and how it
    serializes each `task()` result before writing it to disk isn't stable
    run-to-run: seen in practice as a plain object, a JSON-encoded string
    (since `task()` results are already strings), and a single-key wrapper
    like `{"result": "<json string>"}`. Unwrap strings and single-key dicts
    recursively until something matching the schema's fields turns up.
    """
    if depth > 5:
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return value
        return _unwrap_entrypoint_doc(parsed, depth + 1)
    if isinstance(value, dict):
        if _ENTRYPOINT_DOC_FIELDS <= value.keys():
            return value
        if len(value) == 1:
            (only_value,) = value.values()
            return _unwrap_entrypoint_doc(only_value, depth + 1)
    return value


def parse_entrypoint_doc(text: str) -> EntrypointDoc:
    """Parse a result file written by the orchestrator's dispatch loop."""
    parsed = _unwrap_entrypoint_doc(json.loads(text))
    return EntrypointDoc.model_validate(parsed)


def render_outputs(layout: StateLayout, slugs: list[str]) -> list[dict[str, str]]:
    """Render the final Markdown from the raw per-entrypoint result files.

    Returns the per-entrypoint errors (also written to `_errors.md`) so the
    caller decides how to surface them.
    """
    entries: list[tuple[str, EntrypointDoc]] = []
    errors: list[dict[str, str]] = []

    for slug in slugs:
        result_file = layout.results_dir / f"{slug}.json"
        if not result_file.exists():
            errors.append({"slug": slug, "error": "no result written by orchestrator"})
            continue
        try:
            doc = parse_entrypoint_doc(result_file.read_text())
        except Exception as exc:  # noqa: BLE001 - surfaced to the user, not swallowed
            errors.append({"slug": slug, "error": f"invalid result: {exc}"})
            continue
        entries.append((slug, doc))
        (layout.entrypoints_dir / f"{slug}.md").write_text(
            render_entrypoint_markdown(slug, doc)
        )

    if layout.errors_json.exists():
        try:
            errors.extend(json.loads(layout.errors_json.read_text()))
        except json.JSONDecodeError:
            pass

    (layout.out / "index.md").write_text(render_index(entries))
    (layout.out / "dependencies.md").write_text(render_dependencies(entries))
    errors_file = layout.out / "_errors.md"
    if errors:
        errors_file.write_text(render_errors(errors))
    elif errors_file.exists():
        errors_file.unlink()  # clear a stale file from a previous, failed run
    return errors
