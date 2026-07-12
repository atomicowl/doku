"""Typed agent configuration and response-model resolution."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PermissionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operations: list[str]
    paths: list[str]
    mode: Literal["allow", "deny"]


class AgentConfig(BaseModel):
    """Validated form of an agent's ``config.toml``.

    ``response_format`` remains an alias for backwards compatibility. New
    configs should use ``output`` and ``response_model``.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    description: str | None = None
    role: str | None = None
    output: Literal["text", "structured"] | None = None
    response_model: str | None = None
    response_format: str | None = None
    permissions: list[PermissionConfig] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_output(self):
        model = self.response_model or self.response_format
        if self.response_model and self.response_format:
            raise ValueError("use response_model, not both response_model and response_format")
        if self.output is None:
            self.output = "structured" if model else "text"
        if self.output == "structured" and not model:
            raise ValueError("structured agents require response_model")
        if self.output == "text" and model:
            raise ValueError("text agents cannot declare a response model")
        return self

    @property
    def model_reference(self) -> str | None:
        return self.response_model or self.response_format

    # Keep the small mapping surface used by existing integrations while the
    # rest of the code migrates from untyped TOML dictionaries.
    def __getitem__(self, key: str):
        return getattr(self, key)

    def __setitem__(self, key: str, value) -> None:
        setattr(self, key, value)

    def get(self, key: str, default=None):
        return getattr(self, key, default)


def resolve_response_model(reference: str, agent_dir: Path):
    """Resolve ``package.module:Class`` or local ``file.py:Class``."""
    if ":" not in reference:
        raise ValueError(
            f"response model {reference!r} needs a module, for example models:{reference}"
        )
    else:
        module_ref, class_name = reference.rsplit(":", 1)
        local_file = agent_dir / (module_ref if module_ref.endswith(".py") else f"{module_ref}.py")
        if local_file.is_file():
            module_name = f"_doku_agent_{agent_dir.name}_{local_file.stem}"
            spec = importlib.util.spec_from_file_location(module_name, local_file)
            if spec is None or spec.loader is None:
                raise ImportError(f"cannot load response-model module {local_file}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        else:
            module = importlib.import_module(module_ref)
    model = getattr(module, class_name)
    if not isinstance(model, type) or not issubclass(model, BaseModel):
        raise TypeError(f"{reference!r} does not resolve to a Pydantic BaseModel")
    return model
