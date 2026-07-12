"""`doku <repo> --out <dir>` — CLI entrypoint."""

from __future__ import annotations

import os
import sys
import math
from pathlib import Path

import typer
from dotenv import find_dotenv, load_dotenv

from doku.agent import _AGENTS_DIR, build_workflow_agent, invoke_orchestrator
from doku.progress import RunDisplay
from doku.workflow import discover_named, final_message_text, load_workflow

_WORKFLOWS_DIR = Path.cwd() / "workflows"

# At import time so the values are in place before Typer resolves envvar-bound
# options like DOKU_MODEL. Searches from the working directory upward; real
# environment variables win over .env entries.
load_dotenv(find_dotenv(usecwd=True))

app = typer.Typer(add_completion=False)


def _parse_model_tuning(
    errors: list[str],
) -> tuple[float | None, int, int | None, float | None, str | None]:
    """Optional model tuning from DOKU_MODEL_RPS / DOKU_MODEL_BURST /
    DOKU_MODEL_MAX_RETRIES / DOKU_MODEL_TEMPERATURE /
    DOKU_MODEL_REASONING_EFFORT.

    Unset means no rate limiting / the provider's own retry default. Invalid
    values are reported into `errors` (same stop-with-a-clear-message
    treatment as the required settings).
    """
    rps_raw = os.environ.get("DOKU_MODEL_RPS")
    burst_raw = os.environ.get("DOKU_MODEL_BURST")
    retries_raw = os.environ.get("DOKU_MODEL_MAX_RETRIES")
    temperature_raw = os.environ.get("DOKU_MODEL_TEMPERATURE")
    reasoning_effort = os.environ.get("DOKU_MODEL_REASONING_EFFORT") or None
    model_rps: float | None = None
    model_burst = 1
    max_retries: int | None = None
    temperature: float | None = None
    if rps_raw:
        try:
            model_rps = float(rps_raw)
        except ValueError:
            model_rps = -1.0
        if model_rps <= 0:
            errors.append(f"DOKU_MODEL_RPS must be a positive number, got {rps_raw!r}.")
            model_rps = None
    if burst_raw:
        if not rps_raw:
            errors.append("DOKU_MODEL_BURST requires DOKU_MODEL_RPS to be set.")
        try:
            model_burst = int(burst_raw)
        except ValueError:
            model_burst = -1
        if model_burst < 1:
            errors.append(f"DOKU_MODEL_BURST must be a positive integer, got {burst_raw!r}.")
            model_burst = 1
    if retries_raw:
        try:
            max_retries = int(retries_raw)
        except ValueError:
            max_retries = -1
        if max_retries < 0:
            errors.append(
                f"DOKU_MODEL_MAX_RETRIES must be a non-negative integer, got {retries_raw!r}."
            )
            max_retries = None
    if temperature_raw:
        try:
            temperature = float(temperature_raw)
        except ValueError:
            temperature = -1.0
        if temperature < 0 or not math.isfinite(temperature):
            errors.append(
                "DOKU_MODEL_TEMPERATURE must be a non-negative finite number, "
                f"got {temperature_raw!r}."
            )
            temperature = None
    if reasoning_effort is not None:
        reasoning_effort = reasoning_effort.strip()
        if not reasoning_effort:
            reasoning_effort = None
    return model_rps, model_burst, max_retries, temperature, reasoning_effort


def _echo(message: str) -> None:
    """`typer.echo` plus an explicit flush.

    Progress output is only useful if it appears as it happens rather than
    getting buffered up when stdout isn't a TTY (piped to a file/log).
    """
    typer.echo(message)
    sys.stdout.flush()


@app.command()
def analyze(
    repo: Path = typer.Argument(..., exists=True, file_okay=False, help="Path to the target codebase."),
    out: Path = typer.Option(Path("docs"), "--out", "-o", help="Directory to write generated docs to."),
    model: str | None = typer.Option(None, "--model", envvar="DOKU_MODEL", help="LLM model id, e.g. openrouter:z-ai/glm-5.2. Required (no default): pass the flag or set DOKU_MODEL."),
    concurrency: int = typer.Option(5, "--concurrency", help="Workflow concurrency parameter."),
    chat_completions: bool = typer.Option(False, "--chat-completions", envvar="DOKU_CHAT_COMPLETIONS", help="With openai:* models, use the plain Chat Completions API instead of the OpenAI Responses API — needed for OpenAI-compatible servers (vLLM, Ollama, gateways)."),
    workflow: str = typer.Option("document-codebase", "--workflow", "-w", help="Bundled workflow name or path to a workflow directory."),
    agents_dir: Path = typer.Option(_AGENTS_DIR, "--agents-dir", envvar="DOKU_AGENTS_DIR", help="Directory containing subagents."),
    workflows_dir: Path = typer.Option(_WORKFLOWS_DIR, "--workflows-dir", envvar="DOKU_WORKFLOWS_DIR", help="Directory used to resolve workflow names."),
) -> None:
    """Run a configured agent workflow over REPO."""
    api_key = os.environ.get("DOKU_API_KEY")
    api_base = os.environ.get("DOKU_API_BASE")
    missing = [name for name, value in [("DOKU_API_KEY", api_key), ("DOKU_API_BASE", api_base)] if not value]
    errors = []
    model_rps, model_burst, max_retries, temperature, reasoning_effort = (
        _parse_model_tuning(errors)
    )
    if missing:
        errors.append(
            f"Missing required environment variable(s): {', '.join(missing)}.\n"
            "Set DOKU_API_KEY to your LLM provider API key and DOKU_API_BASE to its "
            "base URL (e.g. https://openrouter.ai/api/v1)."
        )
    if not model:
        errors.append(
            "No model configured: pass --model or set DOKU_MODEL "
            "(e.g. openrouter:z-ai/glm-5.2)."
        )
    if errors:
        typer.echo("\n".join(errors), err=True)
        raise typer.Exit(code=1)

    repo = repo.resolve()
    out = out.resolve()

    try:
        loaded_workflow = load_workflow(discover_named(workflows_dir.resolve(), workflow))
    except Exception as exc:  # noqa: BLE001 - configuration error for the operator
        typer.echo(f"Invalid workflow: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    out.mkdir(parents=True, exist_ok=True)
    state_dir = out / "_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / "run.log"

    _echo(f"Dispatching {model} workflow '{loaded_workflow.config.name}' (concurrency={concurrency})...")
    _echo(f"Full activity log: {log_path}")
    agent = build_workflow_agent(
        workflow=loaded_workflow,
        repo_path=repo,
        output_dir=out,
        agents_dir=agents_dir.resolve(),
        model=model,
        api_key=api_key,
        api_base=api_base,
        concurrency=concurrency,
        chat_completions=chat_completions,
        model_rps=model_rps,
        model_burst=model_burst,
        max_retries=max_retries,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
    )
    # Total is unknown up front: the orchestrator's discovery subagents find
    # the candidates during the run, so the display counts without a bar cap.
    display = RunDisplay(log_path=log_path, title=loaded_workflow.config.name)
    with display:
        result = invoke_orchestrator(agent, display=display)
    _echo(f"{display.completed} completed, {display.failed} failed.")
    summary = final_message_text(result)
    if summary:
        _echo(f"Agent summary: {summary}")

    if loaded_workflow.finalizer:
        final = loaded_workflow.finalizer(out, result)
        if isinstance(final, dict) and final.get("message"):
            _echo(final["message"])
    _echo(f"Workflow output written to {out}")

    # deepagents/langchain_quickjs leave background threads (HTTP connection
    # pools, the QuickJS worker, ...) that don't get joined on their own, so
    # a normal Python exit hangs here indefinitely waiting on them. All work
    # is on disk by this point, so force-exit rather than make every run look
    # like it never finished.
    os._exit(0)


if __name__ == "__main__":
    app()
