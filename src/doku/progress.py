"""Live display + file log for the orchestrator's subagent dispatch loop.

The whole dispatch loop runs inside one opaque `eval` tool call that doesn't
return until every batch finishes, so without this there is no visibility
into what's happening — just silence between "Dispatching..." and the final
summary, however long that takes.

Output is split across two sinks:

- **Terminal**: a live-updating dashboard (overall progress, what the
  orchestrator is doing, the currently running subagents with elapsed time
  and last activity, recent finishes). When stdout is not a TTY it degrades
  to plain one-line lifecycle messages instead.
- **Log file** (`log_path`): the full play-by-play as plain text — the
  orchestrator's reasoning and the dispatch-loop JS it writes, each
  subagent's full prompt (inlined source collapsed), every tool call and
  result, and each dispatch's lifecycle.

Three LangGraph streams feed it (see `invoke_orchestrator`, which routes
them here):

- `custom` events: `langchain_quickjs`'s per-`task()` start/complete/error
  lifecycle, driving the dashboard rows and the progress bar.
- `updates` (with `subgraphs=True`): the orchestrator's own turns
  (namespace `()`) and every subagent's internal turns (child namespaces),
  i.e. what each subagent thinks and which tools it calls.
- `values` for child namespaces: only used to correlate a child namespace
  with its dispatch. The lifecycle events carry the dispatch id and label
  but not the namespace; the child graph's first `values` chunk carries the
  full task prompt as `messages[0]`, and the lifecycle start event's
  `description` is a truncated prefix of that same prompt string —
  prefix-matching the two binds namespace -> dispatch, and gives us the
  *untruncated* prompt to log as a bonus.
"""

from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console, Group
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.syntax import Syntax
from rich.text import Text

#: how many leading lines of the orchestrator's `eval` code to log
MAX_EVAL_LINES = 60

#: prompts without a recognizable inlined-source block get clipped to this
MAX_PROMPT_CHARS = 800

#: finished dispatches kept visible at the bottom of the dashboard
RECENT_FINISHES = 6

#: matches the inlined-source block the orchestrator's prompt template appends
_SOURCE_BLOCK_RE = re.compile(r"\n+Full source of (\S+):\n```")


def text_of(content: Any) -> str:
    """Best-effort plain text from a LangChain message's `.content`."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") in ("text", "reasoning"):
                parts.append(block.get("text") or block.get("reasoning") or "")
        return "\n".join(p for p in parts if p)
    return ""


def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


@dataclass
class _Dispatch:
    """One in-flight `task()` dispatch, keyed by its lifecycle-event id."""

    id: str
    label: str
    description: str
    namespace: tuple | None = None
    tool_calls: int = 0
    row: TaskID | None = None  # its row in the dashboard's "running" list


class RunDisplay:
    """Live dashboard on the terminal, full play-by-play in a log file.

    Use as a context manager around `invoke_orchestrator` so the live view
    starts/stops and the log file closes cleanly.
    """

    def __init__(
        self,
        console: Console | None = None,
        total: int | None = None,
        log_path: Path | None = None,
        preview_chars: int = 220,
    ):
        self.console = console or Console(highlight=False)
        self.log_path = log_path
        self._total = total
        self._preview = preview_chars
        self._dispatches: dict[str, _Dispatch] = {}
        self._by_namespace: dict[tuple, _Dispatch] = {}
        self.dispatched = 0
        self.completed = 0
        self.failed = 0

        self._log_file = None
        self._log: Console | None = None
        if log_path is not None:
            # line-buffered so the log is tail -f-able while the run is live
            self._log_file = open(log_path, "w", buffering=1, errors="replace")
            self._log = Console(
                file=self._log_file, width=100, force_terminal=False, highlight=False
            )

        # dashboard widgets (only rendered when the console is a terminal)
        self._live = None
        self._overall = Progress(
            SpinnerColumn(),
            TextColumn("[bold]documenting[/bold]"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=self.console,
        )
        self._overall_task = self._overall.add_task("", total=total)
        self._running = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}", style="bold"),
            TimeElapsedColumn(),
            TextColumn("{task.fields[activity]}", style="dim"),
            console=self.console,
        )
        self._orchestrator_status = Text("waiting for orchestrator…", style="dim italic")
        self._recent: deque[Text] = deque(maxlen=RECENT_FINISHES)

    # -- lifecycle ----------------------------------------------------------

    def __enter__(self) -> RunDisplay:
        if self.console.is_terminal:
            from rich.live import Live

            self._live = Live(
                self._render(), console=self.console, refresh_per_second=6
            )
            self._live.start()
        return self

    def __exit__(self, *exc_info) -> None:
        if self._live is not None:
            self._live.update(self._render())
            self._live.stop()
            self._live = None
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None
            self._log = None

    # -- dashboard ----------------------------------------------------------

    def _render(self) -> Panel:
        sections: list[Any] = [self._overall, Text()]
        line = Text("  orchestrator │ ", style="bold")
        line.append_text(self._orchestrator_status)
        sections.append(line)
        if self._running.tasks:
            sections.append(Text())
            sections.append(Text("  running:", style="bold"))
            sections.append(self._running)
        if self._recent:
            sections.append(Text())
            sections.append(Text("  recent:", style="bold"))
            sections.extend(self._recent)
        return Panel(
            Group(*sections),
            title="doku — documenting entrypoints",
            subtitle=f"full log: {self.log_path}" if self.log_path else None,
            subtitle_align="left",
            border_style="cyan",
        )

    def _refresh(self) -> None:
        if self._live is not None:
            self._live.update(self._render())

    def _log_print(self, renderable: Any) -> None:
        if self._log is not None:
            self._log.print(renderable)

    def _console_line(self, line: Text) -> None:
        """Plain lifecycle output for non-TTY runs (piped to a file/CI log)."""
        if self._live is None:
            self.console.print(line)

    # -- custom stream: task() lifecycle -------------------------------------

    def on_subagent_event(self, event: dict[str, Any]) -> None:
        phase = event.get("phase")
        event_id = event.get("id")
        if phase == "start":
            label = event.get("label") or event.get("subagent_type") or str(event_id)
            dispatch = _Dispatch(
                id=event_id, label=label, description=event.get("description", "")
            )
            self._dispatches[event_id] = dispatch
            self.dispatched += 1
            dispatch.row = self._running.add_task(label, total=None, activity="starting…")
            count = f"{self.dispatched}/{self._total}" if self._total else str(self.dispatched)
            line = Text("  ▶ ", style="bold cyan")
            line.append(f"[{count}] ", style="bold")
            line.append(label, style="bold")
            self._log_print(line)
            self._console_line(line)
            self._refresh()
        elif phase in ("complete", "error"):
            self._finish_dispatch(event, failed=phase == "error")

    def _finish_dispatch(self, event: dict[str, Any], *, failed: bool) -> None:
        dispatch = self._pop_dispatch(event.get("id"))
        if failed:
            self.failed += 1
        else:
            self.completed += 1
        self._overall.advance(self._overall_task)
        line = Text("  ✗ " if failed else "  ✓ ", style="bold red" if failed else "bold green")
        line.append(dispatch.label)
        if failed:
            line.append(f" ({event.get('duration_ms', 0) / 1000:.1f}s): ", style="dim")
            line.append(_clip(str(event.get("error")), self._preview), style="red")
        else:
            line.append(f" ({event.get('duration_ms', 0) / 1000:.1f}s", style="dim")
            if dispatch.tool_calls:
                line.append(f", {dispatch.tool_calls} tool call(s)", style="dim")
            line.append(")", style="dim")
        self._recent.append(line)
        self._log_print(line)
        self._console_line(line)
        self._refresh()

    def _pop_dispatch(self, event_id: str) -> _Dispatch:
        dispatch = self._dispatches.pop(event_id, None)
        if dispatch is None:
            return _Dispatch(id=str(event_id), label=str(event_id), description="")
        if dispatch.namespace is not None:
            self._by_namespace.pop(dispatch.namespace, None)
        if dispatch.row is not None:
            self._running.remove_task(dispatch.row)
            dispatch.row = None
        return dispatch

    # -- child values stream: namespace -> dispatch correlation --------------

    def on_values(self, namespace: tuple, state: Any) -> None:
        """Bind a child namespace to its dispatch via the task prompt.

        Only the first `values` chunk per namespace matters: its
        `messages[0]` is the full prompt the subagent received, and the
        dispatch whose (truncated) `description` is a prefix of it is the one
        running under this namespace.
        """
        if not namespace or namespace in self._by_namespace:
            return
        messages = state.get("messages") if isinstance(state, dict) else None
        if not messages:
            return
        prompt = text_of(getattr(messages[0], "content", None))
        if not prompt:
            return
        for dispatch in self._dispatches.values():
            if dispatch.namespace is None and dispatch.description and prompt.startswith(
                dispatch.description
            ):
                dispatch.namespace = namespace
                self._by_namespace[namespace] = dispatch
                self._log_print(self._prompt_panel(dispatch.label, prompt))
                return

    def _prompt_panel(self, label: str, prompt: str) -> Panel:
        """The prompt a subagent received, with any inlined source collapsed
        to a one-line note (it is multi-KB and already on disk)."""
        body = Text()
        match = _SOURCE_BLOCK_RE.search(prompt)
        if match:
            body.append(prompt[: match.start()].strip())
            source = prompt[match.end() :].strip().removesuffix("```").rstrip()
            n_lines = source.count("\n") + 1 if source else 0
            body.append(
                f"\n\n[+ full source of {match.group(1)} inlined — {n_lines} lines]",
                style="dim italic",
            )
        else:
            body.append(_clip(prompt, MAX_PROMPT_CHARS))
        return Panel(
            body,
            title=f"prompt → {label}",
            title_align="left",
            border_style="magenta",
        )

    # -- updates stream: orchestrator + subagent turns ------------------------

    def on_update(self, namespace: tuple, node_name: str, update: Any) -> None:
        messages = update.get("messages") if isinstance(update, dict) else None
        if not messages:
            return
        if namespace:
            dispatch = self._by_namespace.get(namespace)
            speaker = dispatch.label if dispatch else "subagent"
            for message in messages:
                self._handle_message(message, speaker, dispatch, indent="    ")
        else:
            for message in messages:
                self._handle_message(message, "orchestrator", None, indent="  ")
        self._refresh()

    def _handle_message(
        self, message: Any, speaker: str, dispatch: _Dispatch | None, indent: str
    ) -> None:
        msg_type = getattr(message, "type", None)
        if msg_type == "ai":
            text = text_of(getattr(message, "content", None))
            if text:
                line = Text(indent)
                line.append(speaker, style="bold" if dispatch is None else "")
                line.append(" │ ", style="dim")
                line.append(_clip(text, self._preview * 2), style="dim italic")
                self._log_print(line)
                if dispatch is None:
                    self._orchestrator_status = Text(_clip(text, 80), style="dim italic")
                else:
                    self._set_activity(dispatch, _clip(text, 60))
            for call in getattr(message, "tool_calls", None) or []:
                if dispatch is not None:
                    dispatch.tool_calls += 1
                self._handle_tool_call(call, speaker, dispatch, indent)
        elif msg_type == "tool":
            name = getattr(message, "name", None) or "tool"
            text = text_of(getattr(message, "content", None))
            is_error = getattr(message, "status", None) == "error"
            line = Text(indent)
            line.append(speaker)
            line.append(" ✗ " if is_error else " ← ", style="bold red" if is_error else "dim")
            line.append(f"{name}: ", style="red" if is_error else "dim")
            line.append(_clip(text, self._preview), style="red" if is_error else "dim")
            self._log_print(line)

    def _handle_tool_call(
        self, call: dict[str, Any], speaker: str, dispatch: _Dispatch | None, indent: str
    ) -> None:
        name = call.get("name", "?")
        args = call.get("args") or {}
        if name == "eval" and "code" in args and dispatch is None:
            code = args["code"]
            self._log_print(self._eval_panel(code))
            n_lines = len(code.strip().splitlines())
            self._orchestrator_status = Text(
                f"running dispatch loop (eval, {n_lines} lines of JS)…"
            )
            return
        try:
            rendered = json.dumps(args, default=str)
        except TypeError:
            rendered = str(args)
        line = Text(indent)
        line.append(speaker)
        line.append(" → ", style="bold cyan")
        line.append(name, style="cyan")
        line.append(" ")
        line.append(_clip(rendered, self._preview), style="dim")
        self._log_print(line)
        if dispatch is None:
            self._orchestrator_status = Text(f"→ {name} {_clip(rendered, 60)}")
        else:
            self._set_activity(dispatch, f"→ {name} ({dispatch.tool_calls} calls)")

    def _set_activity(self, dispatch: _Dispatch | None, activity: str) -> None:
        if dispatch is not None and dispatch.row is not None:
            self._running.update(dispatch.row, activity=activity)

    def _eval_panel(self, code: str) -> Panel:
        """The dispatch-loop JavaScript the orchestrator wrote, highlighted.

        This code *is* the dynamic subagent flow — it decides batching, what
        prompt each subagent gets, and where results land — so it gets a full
        panel in the log rather than a one-line preview.
        """
        lines = code.strip().splitlines()
        shown = "\n".join(lines[:MAX_EVAL_LINES])
        if len(lines) > MAX_EVAL_LINES:
            shown += f"\n// … +{len(lines) - MAX_EVAL_LINES} more lines"
        return Panel(
            Syntax(shown, "javascript", line_numbers=True, word_wrap=True),
            title=f"orchestrator → eval · dispatch loop ({len(lines)} lines)",
            title_align="left",
            border_style="cyan",
        )
