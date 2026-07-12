"""On-disk pipeline state and final output generation.

Everything the run persists lives under the docs output directory:
`_state/` is the contract with the orchestrator agent (manifest in, raw
results out), and the rendered Markdown next to it is the user-facing
product. This module owns that layout and every read/write against it, so
the CLI stays argument parsing and wiring.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from doku.detectors import EntrypointCandidate
from doku.models import EntrypointDoc
from doku.progress import text_of
from doku.render import (
    render_dependencies,
    render_entrypoint_markdown,
    render_errors,
    render_index,
)

# Cap how much source text gets inlined per candidate so one pathologically
# large file can't blow out an entire dispatch batch's context.
MAX_SOURCE_CHARS = 20_000


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
    def sources_dir(self) -> Path:
        return self.state_dir / "sources"

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
        for directory in (self.entrypoints_dir, self.results_dir, self.sources_dir):
            directory.mkdir(parents=True, exist_ok=True)


def write_manifest(
    repo: Path, candidates: list[EntrypointCandidate], layout: StateLayout
) -> None:
    """Write the source-free candidate manifest and its side-car source files."""
    layout.entrypoints_json.write_text(
        json.dumps(candidates_to_json(repo, candidates, layout.sources_dir), indent=2)
    )


def candidates_to_json(
    repo: Path, candidates: list[EntrypointCandidate], sources_dir: Path
) -> list[dict]:
    """Serialize candidates for the orchestrator, and write one small source
    file per unique source file under `sources_dir`.

    Each candidate's manifest entry carries `source_ref`, an index into
    `sources_dir/<source_ref>.json` (`{"source_lines": [...]}` — one array
    entry per line) rather than the source inlined in the manifest itself.
    Two problems drove this split, both discovered by watching the
    orchestrator's own reasoning on real (non-tiny) repos:

    - `read_file` reformats content as `cat -n` lines and splits any single
      physical line over 5000 chars into numbered continuation chunks
      (`13.1`, `13.2`, ...) that have to be rejoined *without* a separator —
      a whole file inlined as one JSON string is exactly such an oversized
      line for almost any real source file, and getting that reconstruction
      right is the kind of fiddly parsing a model reliably gets wrong.
    - Tool results over ~80,000 chars get evicted to a side file by
      deepagents' own context-management, forcing yet another layer of
      pagination the model has to discover and handle itself.

    A single manifest with every candidate's source inlined hits both once a
    repo has more than a handful of entrypoints. Keeping the manifest itself
    source-free (small regardless of repo size) and each source file small
    (one file's contents, capped at `MAX_SOURCE_CHARS`) keeps every read
    comfortably under both thresholds.
    """
    file_to_ref: dict[str, int] = {}
    payload = []
    for candidate in candidates:
        if candidate.file not in file_to_ref:
            file_to_ref[candidate.file] = len(file_to_ref)
        entry = candidate.to_dict()
        entry["source_ref"] = file_to_ref[candidate.file]
        payload.append(entry)

    for file, ref in file_to_ref.items():
        text = (repo / file).read_text(errors="replace")
        if len(text) > MAX_SOURCE_CHARS:
            text = text[:MAX_SOURCE_CHARS] + "\n... (truncated)"
        (sources_dir / f"{ref}.json").write_text(
            json.dumps({"source_lines": text.split("\n")})
        )

    return payload


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


def render_outputs(
    layout: StateLayout, candidates: list[EntrypointCandidate]
) -> list[dict[str, str]]:
    """Render the final Markdown from the raw per-entrypoint result files.

    Returns the per-entrypoint errors (also written to `_errors.md`) so the
    caller decides how to surface them.
    """
    entries: list[tuple[str, EntrypointDoc]] = []
    errors: list[dict[str, str]] = []

    for candidate in candidates:
        result_file = layout.results_dir / f"{candidate.slug}.json"
        if not result_file.exists():
            errors.append({"slug": candidate.slug, "error": "no result written by orchestrator"})
            continue
        try:
            doc = parse_entrypoint_doc(result_file.read_text())
        except Exception as exc:  # noqa: BLE001 - surfaced to the user, not swallowed
            errors.append({"slug": candidate.slug, "error": f"invalid result: {exc}"})
            continue
        entries.append((candidate.slug, doc))
        (layout.entrypoints_dir / f"{candidate.slug}.md").write_text(
            render_entrypoint_markdown(candidate.slug, doc)
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


def write_empty_docs(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.md").write_text(render_index([]))
    (out / "dependencies.md").write_text(render_dependencies([]))
