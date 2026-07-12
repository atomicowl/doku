"""Commands for creating and validating external workflows."""

from __future__ import annotations

import re
from pathlib import Path

import typer

from doku.agent import _AGENTS_DIR, _load_subagents
from doku.workflow import load_workflow

app = typer.Typer(add_completion=False, help="Create and validate doku workflows.")


@app.command()
def create(
    name: str,
    directory: Path = typer.Option(Path("workflows"), "--dir"),
) -> None:
    """Create a text-output workflow skeleton."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise typer.BadParameter("name may contain only letters, numbers, '-' and '_'")
    target = directory / name.replace("-", "_")
    if target.exists():
        typer.echo(f"Workflow already exists: {target}", err=True)
        raise typer.Exit(1)
    target.mkdir(parents=True)
    (target / "config.toml").write_text(
        f'name = "{name}"\n'
        'description = "Describe this workflow."\n'
        'subagents = []\n'
        'output = "text"\n'
    )
    (target / "prompt.md").write_text(
        "Run the configured workflow over the codebase mounted at `/repo`.\n"
    )
    typer.echo(f"Created {target}")


@app.command()
def validate(
    workflow_dir: Path,
    agents_dir: Path = typer.Option(_AGENTS_DIR, "--agents-dir", envvar="DOKU_AGENTS_DIR"),
) -> None:
    """Validate a workflow and all of its referenced subagents."""
    try:
        workflow = load_workflow(workflow_dir)
        agents, _routes, _roles = _load_subagents(agents_dir)
        names = {agent["name"] for agent in agents}
        missing = [name for name in workflow.config.subagents if name not in names]
        if missing:
            raise ValueError(f"missing subagent(s): {', '.join(missing)}")
    except Exception as exc:  # noqa: BLE001
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        f"Valid workflow '{workflow.config.name}' with "
        f"{len(workflow.config.subagents)} subagent(s)"
    )


if __name__ == "__main__":
    app()
