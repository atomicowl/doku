"""Commands for scaffolding and validating doku agents."""

from __future__ import annotations

import re
from pathlib import Path

import typer

from doku.agent import _AGENTS_DIR, _load_agent, _load_subagents

app = typer.Typer(add_completion=False, help="Create and validate doku subagents.")


@app.command()
def create(
    name: str = typer.Argument(..., help="Agent name (letters, numbers, '-' and '_')."),
    output: str = typer.Option("text", help="Output type: text or structured."),
    role: str = typer.Option("custom", help="Workflow role label."),
    model: str | None = typer.Option(None, help="Pydantic model class for structured output."),
    agents_dir: Path = typer.Option(_AGENTS_DIR, hidden=True),
) -> None:
    """Create a new subagent folder without overwriting existing files."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise typer.BadParameter("name may contain only letters, numbers, '-' and '_'")
    if output not in {"text", "structured"}:
        raise typer.BadParameter("output must be text or structured")
    if output == "structured" and not model:
        raise typer.BadParameter("--model is required for structured output")
    if model and model.startswith("models:"):
        class_name = model.split(":", 1)[1]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", class_name):
            raise typer.BadParameter("the local model class name is invalid")
    target = agents_dir / "subagents" / name
    if target.exists():
        typer.echo(f"Agent already exists: {target}", err=True)
        raise typer.Exit(1)
    target.mkdir(parents=True)
    lines = [
        f'name = "{name}"',
        f'role = "{role}"',
        f'description = "Describe what {name} does."',
        f'output = "{output}"',
    ]
    if model:
        lines.append(f'response_model = "{model}"')
    lines += [
        "",
        "[[permissions]]",
        'operations = ["read"]',
        'paths = ["/repo", "/repo/**"]',
        'mode = "allow"',
        "",
        "[[permissions]]",
        'operations = ["read", "write"]',
        'paths = ["/**"]',
        'mode = "deny"',
    ]
    (target / "config.toml").write_text("\n".join(lines) + "\n")
    (target / "prompt.md").write_text(f"You are the {name} subagent.\n")
    if model and model.startswith("models:"):
        class_name = model.split(":", 1)[1]
        (target / "models.py").write_text(
            "from pydantic import BaseModel\n\n\n"
            f"class {class_name}(BaseModel):\n"
            '    """Replace these placeholder fields with the agent contract."""\n\n'
            "    result: str\n"
        )
    typer.echo(f"Created {target}")


@app.command()
def validate(agents_dir: Path = typer.Option(_AGENTS_DIR, hidden=True)) -> None:
    """Validate all configs, prompts, skills, names, roles, and response models."""
    errors: list[str] = []
    names: set[str] = set()
    folders = [agents_dir / "orchestrator", *sorted((agents_dir / "subagents").iterdir())]
    for folder in folders:
        if not (folder / "config.toml").exists():
            continue
        try:
            config, _ = _load_agent(folder)
            if config.name in names:
                raise ValueError(f"duplicate agent name {config.name!r}")
            names.add(config.name)
            for skill in config.skills:
                if not (folder / skill).is_dir():
                    raise ValueError(f"missing skills directory {skill!r}")
        except Exception as exc:  # noqa: BLE001 - validation reports all failures
            errors.append(f"{folder.name}: {exc}")
    try:
        _load_subagents(agents_dir)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"workflow: {exc}")
    if errors:
        for error in errors:
            typer.echo(error, err=True)
        raise typer.Exit(1)
    typer.echo(f"Valid: {len(names)} agent(s)")


if __name__ == "__main__":
    app()
