"""Static-analysis entrypoint detectors.

Discovery of entrypoints (REST/SOAP APIs, Kafka consumers, ...) is done
without an LLM, so that coverage across a whole repo is guaranteed rather
than left to agent judgment. Each language/framework gets its own
`Detector` implementation; `run_detectors` aggregates their output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, Sequence

EntrypointType = Literal["REST", "SOAP", "KAFKA"]

#: directories that never contain application entrypoint source
EXCLUDED_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "target",
    "build",
    "out",
    "bin",
    "dist",
    "node_modules",
}


@dataclass(frozen=True)
class EntrypointCandidate:
    """One discovered entrypoint, ready to be handed to a documenter subagent."""

    type: EntrypointType
    file: str  # path relative to the scanned repo root, forward-slash separated
    line: int  # 1-based line number of the method declaration
    class_name: str
    method_name: str
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def slug(self) -> str:
        return f"{self.type.lower()}-{self.class_name}-{self.method_name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "file": self.file,
            "line": self.line,
            "class_name": self.class_name,
            "method_name": self.method_name,
            "meta": self.meta,
            "slug": self.slug,
        }


class Detector(Protocol):
    """A pluggable static-analysis detector for one language/framework."""

    def detect(self, repo_root: Path) -> list[EntrypointCandidate]: ...


def is_excluded_dir(path: Path) -> bool:
    return any(part in EXCLUDED_DIR_NAMES for part in path.parts)


def run_detectors(
    repo_root: Path, detectors: Sequence[Detector]
) -> list[EntrypointCandidate]:
    """Run every detector over `repo_root` and return a stably sorted list."""
    candidates: list[EntrypointCandidate] = []
    for detector in detectors:
        candidates.extend(detector.detect(repo_root))
    candidates.sort(key=lambda c: (c.file, c.line))
    return candidates
