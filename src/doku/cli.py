"""`doku <repo> --out <dir>` — CLI entrypoint."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer
from dotenv import find_dotenv, load_dotenv

from doku.agent import build_orchestrator, invoke_orchestrator
from doku.progress import RunDisplay
from doku.state import (
    StateLayout,
    final_message_text,
    read_manifest,
    render_outputs,
)

# At import time so the values are in place before Typer resolves envvar-bound
# options like DOKU_MODEL. Searches from the working directory upward; real
# environment variables win over .env entries.
load_dotenv(find_dotenv(usecwd=True))

app = typer.Typer(add_completion=False)


def _parse_model_tuning(errors: list[str]) -> tuple[float | None, int, int | None]:
    """Optional model tuning from DOKU_MODEL_RPS / DOKU_MODEL_BURST /
    DOKU_MODEL_MAX_RETRIES.

    Unset means no rate limiting / the provider's own retry default. Invalid
    values are reported into `errors` (same stop-with-a-clear-message
    treatment as the required settings).
    """
    rps_raw = os.environ.get("DOKU_MODEL_RPS")
    burst_raw = os.environ.get("DOKU_MODEL_BURST")
    retries_raw = os.environ.get("DOKU_MODEL_MAX_RETRIES")
    model_rps: float | None = None
    model_burst = 1
    max_retries: int | None = None
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
    return model_rps, model_burst, max_retries


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
    concurrency: int = typer.Option(5, "--concurrency", help="Max entrypoints documented in parallel."),
    chat_completions: bool = typer.Option(False, "--chat-completions", envvar="DOKU_CHAT_COMPLETIONS", help="With openai:* models, use the plain Chat Completions API instead of the OpenAI Responses API — needed for OpenAI-compatible servers (vLLM, Ollama, gateways)."),
) -> None:
    """Discover entrypoints in REPO and generate documentation into OUT."""
    api_key = os.environ.get("DOKU_API_KEY")
    api_base = os.environ.get("DOKU_API_BASE")
    missing = [name for name, value in [("DOKU_API_KEY", api_key), ("DOKU_API_BASE", api_base)] if not value]
    errors = []
    model_rps, model_burst, max_retries = _parse_model_tuning(errors)
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

    layout = StateLayout(out)
    layout.ensure_dirs()

    _echo(f"Dispatching {model} orchestrator (concurrency={concurrency})...")
    _echo(f"Full activity log: {layout.log_path}")
    agent = build_orchestrator(
        repo_path=repo,
        docs_dir=out,
        model=model,
        api_key=api_key,
        api_base=api_base,
        concurrency=concurrency,
        chat_completions=chat_completions,
        model_rps=model_rps,
        model_burst=model_burst,
        max_retries=max_retries,
    )
    # Total is unknown up front: the orchestrator's discovery subagents find
    # the candidates during the run, so the display counts without a bar cap.
    display = RunDisplay(log_path=layout.log_path)
    with display:
        result = invoke_orchestrator(agent, display=display)
    _echo(f"{display.completed} completed, {display.failed} failed.")
    summary = final_message_text(result)
    if summary:
        _echo(f"Agent summary: {summary}")

    candidates = read_manifest(layout)
    _echo(f"Discovered {len(candidates)} entrypoint(s).")
    errors = render_outputs(layout, [candidate["slug"] for candidate in candidates])
    if errors:
        _echo(f"{len(errors)} entrypoint(s) failed to document; see _errors.md")
    _echo(f"Docs written to {out}")

    # deepagents/langchain_quickjs leave background threads (HTTP connection
    # pools, the QuickJS worker, ...) that don't get joined on their own, so
    # a normal Python exit hangs here indefinitely waiting on them. All work
    # is on disk by this point, so force-exit rather than make every run look
    # like it never finished.
    os._exit(0)


if __name__ == "__main__":
    app()
