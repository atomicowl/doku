"""Task-agnostic workflow definitions and dynamic extension loading."""

from __future__ import annotations

import importlib
import importlib.util
import sys
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from doku.agent_config import resolve_response_model
from doku.progress import text_of


class WorkflowConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    prompt: str = "prompt.md"
    subagents: list[str]
    output: Literal["text", "structured"] = "text"
    response_model: str | None = None
    finalizer: str | None = None
    prompt_variables: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_output(self):
        if self.output == "structured" and not self.response_model:
            raise ValueError("structured workflows require response_model")
        if self.output == "text" and self.response_model:
            raise ValueError("text workflows cannot declare response_model")
        if len(self.subagents) != len(set(self.subagents)):
            raise ValueError("workflow subagents must be unique")
        return self


class LoadedWorkflow(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    config: WorkflowConfig
    directory: Path
    prompt_template: str
    response_model: Any = None
    finalizer: Any = None

    def prompt(self, **runtime_variables: Any) -> str:
        values = {**self.config.prompt_variables, **runtime_variables}
        result = self.prompt_template
        for key, value in values.items():
            result = result.replace(f"__{key}__", str(value))
        return result


def _load_local_symbol(reference: str, directory: Path):
    module_ref, symbol = reference.rsplit(":", 1)
    local_file = directory / (module_ref if module_ref.endswith(".py") else f"{module_ref}.py")
    if local_file.is_file():
        module_name = f"_doku_workflow_{directory.name}_{local_file.stem}"
        spec = importlib.util.spec_from_file_location(module_name, local_file)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load workflow module {local_file}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    else:
        module = importlib.import_module(module_ref)
    return getattr(module, symbol)


def load_workflow(directory: Path) -> LoadedWorkflow:
    directory = directory.resolve()
    config = WorkflowConfig.model_validate(
        tomllib.loads((directory / "config.toml").read_text())
    )
    prompt_path = (directory / config.prompt).resolve()
    prompt = prompt_path.read_text()
    response_model = (
        resolve_response_model(config.response_model, directory)
        if config.response_model
        else None
    )
    finalizer = _load_local_symbol(config.finalizer, directory) if config.finalizer else None
    return LoadedWorkflow(
        config=config,
        directory=directory,
        prompt_template=prompt,
        response_model=response_model,
        finalizer=finalizer,
    )


def discover_named(root: Path, name_or_path: str) -> Path:
    """Resolve a workflow by directory path or by name under ``root``."""
    candidate = Path(name_or_path)
    if candidate.is_dir():
        return candidate.resolve()
    named = root / name_or_path.replace("-", "_")
    if (named / "config.toml").is_file():
        return named.resolve()
    raise FileNotFoundError(f"workflow {name_or_path!r} not found under {root}")


def final_message_text(result) -> str | None:
    """Return the final workflow message without assuming an output schema."""
    messages = result.get("messages") if isinstance(result, dict) else None
    if not messages:
        return None
    last = messages[-1]
    content = getattr(last, "content", None) if not isinstance(last, dict) else last.get("content")
    return text_of(content) or None
