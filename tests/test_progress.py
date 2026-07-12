import io

from rich.console import Console

from doku.progress import RunDisplay


def make_display(tmp_path, total=None, width=200):
    """Non-TTY console (plain lifecycle lines) + a real log file."""
    console = Console(file=io.StringIO(), width=width, force_terminal=False)
    return RunDisplay(console=console, total=total, log_path=tmp_path / "run.log")


def console_output(display):
    return display.console.file.getvalue()


def log_output(display):
    return (display.log_path).read_text()


class _FakeAIMessage:
    type = "ai"

    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _FakeToolMessage:
    type = "tool"

    def __init__(self, name, content, status="success"):
        self.name = name
        self.content = content
        self.status = status


class _FakeHumanMessage:
    type = "human"

    def __init__(self, content):
        self.content = content


# -- task() lifecycle ---------------------------------------------------------


def test_lifecycle_lines_go_to_console_and_log(tmp_path):
    display = make_display(tmp_path, total=2)

    display.on_subagent_event({"phase": "start", "id": "a1", "label": "rest-Foo-bar", "subagent_type": "entrypoint-documenter", "description": "Document this REST entrypoint..."})
    display.on_subagent_event({"phase": "complete", "id": "a1", "duration_ms": 1234})
    display.on_subagent_event({"phase": "start", "id": "a2", "label": "kafka-Baz-onMsg", "subagent_type": "entrypoint-documenter", "description": "Document this KAFKA entrypoint..."})
    display.on_subagent_event({"phase": "error", "id": "a2", "duration_ms": 42, "error": "boom"})

    for out in (console_output(display), log_output(display)):
        assert "▶ [1/2] rest-Foo-bar" in out
        assert "✓ rest-Foo-bar (1.2s)" in out
        assert "▶ [2/2] kafka-Baz-onMsg" in out
        assert "✗ kafka-Baz-onMsg (0.0s): boom" in out
    assert display.dispatched == 2
    assert display.completed == 1
    assert display.failed == 1


def test_falls_back_to_subagent_type_when_label_missing(tmp_path):
    display = make_display(tmp_path)

    display.on_subagent_event({"phase": "start", "id": "a1", "subagent_type": "entrypoint-documenter", "description": "d"})

    assert "▶ [1] entrypoint-documenter" in console_output(display)


def test_falls_back_to_event_id_when_dispatch_unseen(tmp_path):
    display = make_display(tmp_path)

    display.on_subagent_event({"phase": "complete", "id": "unseen-id", "duration_ms": 5})

    assert "✓ unseen-id" in console_output(display)
    assert display.completed == 1


def test_unknown_phase_is_ignored(tmp_path):
    display = make_display(tmp_path)

    display.on_subagent_event({"phase": "something-else", "id": "a1"})

    assert console_output(display) == ""
    assert display.completed == 0
    assert display.failed == 0


def test_running_rows_track_dispatch_lifecycle(tmp_path):
    display = make_display(tmp_path, total=2)

    display.on_subagent_event({"phase": "start", "id": "a1", "label": "rest-Foo-bar", "subagent_type": "entrypoint-documenter", "description": "d1"})
    display.on_subagent_event({"phase": "start", "id": "a2", "label": "kafka-Baz-onMsg", "subagent_type": "entrypoint-documenter", "description": "d2"})
    assert [t.description for t in display._running.tasks] == ["rest-Foo-bar", "kafka-Baz-onMsg"]

    display.on_subagent_event({"phase": "complete", "id": "a1", "duration_ms": 10})
    assert [t.description for t in display._running.tasks] == ["kafka-Baz-onMsg"]


# -- orchestrator turns (namespace ()) — full detail goes to the log ----------


def test_orchestrator_reasoning_logged_not_printed(tmp_path):
    display = make_display(tmp_path)

    display.on_update((), "model", {"messages": [_FakeAIMessage(content="I'll read the candidate list first.")]})

    assert "I'll read the candidate list first." in log_output(display)
    assert console_output(display) == ""


def test_renders_eval_tool_call_as_code_panel_in_log(tmp_path):
    display = make_display(tmp_path)

    code = "const x = 1;\nconst y = 2;\ny;"
    call = {"name": "eval", "args": {"code": code}}
    display.on_update((), "model", {"messages": [_FakeAIMessage(tool_calls=[call])]})

    out = log_output(display)
    assert "dispatch loop (3 lines)" in out
    assert "const x = 1;" in out


def test_eval_panel_truncates_long_code(tmp_path):
    from doku.progress import MAX_EVAL_LINES

    display = make_display(tmp_path)

    code = "\n".join(f"const v{i} = {i};" for i in range(MAX_EVAL_LINES + 10))
    display.on_update((), "model", {"messages": [_FakeAIMessage(tool_calls=[{"name": "eval", "args": {"code": code}}])]})

    out = log_output(display)
    assert "+10 more lines" in out
    assert f"const v{MAX_EVAL_LINES + 5}" not in out


def test_logs_non_eval_tool_call_args(tmp_path):
    display = make_display(tmp_path)

    call = {"name": "read_file", "args": {"file_path": "/_state/entrypoints.json"}}
    display.on_update((), "model", {"messages": [_FakeAIMessage(tool_calls=[call])]})

    out = log_output(display)
    assert "orchestrator → read_file" in out
    assert '"file_path": "/_state/entrypoints.json"' in out


def test_logs_tool_result_and_marks_errors(tmp_path):
    display = make_display(tmp_path)

    display.on_update((), "tools", {"messages": [_FakeToolMessage("eval", "documented=4 failed=0")]})
    display.on_update((), "tools", {"messages": [_FakeToolMessage("eval", "TypeError: boom", status="error")]})

    out = log_output(display)
    assert "orchestrator ← eval: documented=4 failed=0" in out
    assert "orchestrator ✗ eval: TypeError: boom" in out


def test_ignores_updates_without_messages(tmp_path):
    display = make_display(tmp_path)

    display.on_update((), "some_node", {"todos": []})

    assert log_output(display) == ""


def test_extracts_text_from_content_block_list(tmp_path):
    display = make_display(tmp_path)

    content = [{"type": "text", "text": "Planning the dispatch loop now."}]
    display.on_update((), "model", {"messages": [_FakeAIMessage(content=content)]})

    assert "Planning the dispatch loop now." in log_output(display)


def test_orchestrator_status_reflects_latest_activity(tmp_path):
    display = make_display(tmp_path)

    display.on_update((), "model", {"messages": [_FakeAIMessage(content="Reading the manifest.")]})
    assert "Reading the manifest." in display._orchestrator_status.plain

    display.on_update((), "model", {"messages": [_FakeAIMessage(tool_calls=[{"name": "eval", "args": {"code": "1;\n2;"}}])]})
    assert "dispatch loop (eval, 2 lines of JS)" in display._orchestrator_status.plain


# -- namespace correlation + subagent turns -----------------------------------

NS = ("tools:d009bdb6-b754-d909-6c18-20a9c8731c25",)
PROMPT = (
    "Document this REST entrypoint: class Foo, method bar, file /repo/Foo.java "
    "around line 3.\n\nFull source of Foo.java:\n```\nclass Foo {\n  void bar() {}\n}\n```"
)


def _start_dispatch(display, event_id="a1", label="rest-Foo-bar", description=PROMPT[:200]):
    display.on_subagent_event({"phase": "start", "id": event_id, "label": label, "subagent_type": "entrypoint-documenter", "description": description})


def test_values_chunk_binds_namespace_and_logs_prompt_panel(tmp_path):
    display = make_display(tmp_path)
    _start_dispatch(display)

    display.on_values(NS, {"messages": [_FakeHumanMessage(PROMPT)]})

    out = log_output(display)
    assert "prompt → rest-Foo-bar" in out
    assert "Document this REST entrypoint" in out
    # the inlined source block is collapsed to a note, not dumped verbatim
    assert "full source of Foo.java inlined — 3 lines" in out
    assert "void bar()" not in out


def test_subagent_tool_calls_are_attributed_to_their_dispatch(tmp_path):
    display = make_display(tmp_path)
    _start_dispatch(display)
    display.on_values(NS, {"messages": [_FakeHumanMessage(PROMPT)]})

    call = {"name": "read_file", "args": {"file_path": "/repo/Foo.java"}}
    display.on_update(NS, "model", {"messages": [_FakeAIMessage(content="Checking imports.", tool_calls=[call])]})
    display.on_update(NS, "tools", {"messages": [_FakeToolMessage("read_file", "class Foo {}")]})

    out = log_output(display)
    assert "rest-Foo-bar │ Checking imports." in out
    assert "rest-Foo-bar → read_file" in out
    assert "rest-Foo-bar ← read_file: class Foo {}" in out


def test_subagent_activity_shown_on_running_row(tmp_path):
    display = make_display(tmp_path)
    _start_dispatch(display)
    display.on_values(NS, {"messages": [_FakeHumanMessage(PROMPT)]})

    call = {"name": "read_file", "args": {"file_path": "/repo/Foo.java"}}
    display.on_update(NS, "model", {"messages": [_FakeAIMessage(tool_calls=[call])]})

    (task,) = display._running.tasks
    assert task.fields["activity"] == "→ read_file (1 calls)"


def test_completion_reports_subagent_tool_call_count(tmp_path):
    display = make_display(tmp_path)
    _start_dispatch(display)
    display.on_values(NS, {"messages": [_FakeHumanMessage(PROMPT)]})
    call = {"name": "read_file", "args": {"file_path": "/repo/Foo.java"}}
    display.on_update(NS, "model", {"messages": [_FakeAIMessage(tool_calls=[call])]})

    display.on_subagent_event({"phase": "complete", "id": "a1", "duration_ms": 5000})

    assert "✓ rest-Foo-bar (5.0s, 1 tool call(s))" in console_output(display)


def test_concurrent_dispatches_bind_to_distinct_namespaces(tmp_path):
    display = make_display(tmp_path)
    other_ns = NS + ("1",)
    other_prompt = "Document this KAFKA entrypoint: class Baz, method onMsg."
    _start_dispatch(display, event_id="a1", label="rest-Foo-bar")
    _start_dispatch(display, event_id="a2", label="kafka-Baz-onMsg", description=other_prompt)

    display.on_values(other_ns, {"messages": [_FakeHumanMessage(other_prompt)]})
    display.on_values(NS, {"messages": [_FakeHumanMessage(PROMPT)]})
    display.on_update(other_ns, "model", {"messages": [_FakeAIMessage(tool_calls=[{"name": "ls", "args": {"path": "/repo/"}}])]})

    out = log_output(display)
    assert "kafka-Baz-onMsg → ls" in out
    assert "rest-Foo-bar → ls" not in out


def test_repeated_values_chunks_only_log_prompt_once(tmp_path):
    display = make_display(tmp_path)
    _start_dispatch(display)

    display.on_values(NS, {"messages": [_FakeHumanMessage(PROMPT)]})
    display.on_values(NS, {"messages": [_FakeHumanMessage(PROMPT), _FakeAIMessage(content="hi")]})

    assert log_output(display).count("prompt → rest-Foo-bar") == 1


def test_update_for_unbound_namespace_uses_generic_speaker(tmp_path):
    display = make_display(tmp_path)

    display.on_update(NS, "model", {"messages": [_FakeAIMessage(tool_calls=[{"name": "ls", "args": {}}])]})

    assert "subagent → ls" in log_output(display)


def test_prompt_without_source_block_is_clipped_not_collapsed(tmp_path):
    display = make_display(tmp_path)
    prompt = "Document this KAFKA entrypoint: class Baz, method onMsg."
    _start_dispatch(display, description=prompt)

    display.on_values(NS, {"messages": [_FakeHumanMessage(prompt)]})

    out = log_output(display)
    assert "prompt → rest-Foo-bar" in out
    assert "class Baz, method onMsg" in out


def test_works_without_log_path(tmp_path):
    console = Console(file=io.StringIO(), width=200, force_terminal=False)
    display = RunDisplay(console=console, total=1)

    _start_dispatch(display)
    display.on_values(NS, {"messages": [_FakeHumanMessage(PROMPT)]})
    display.on_update(NS, "model", {"messages": [_FakeAIMessage(tool_calls=[{"name": "ls", "args": {}}])]})
    display.on_subagent_event({"phase": "complete", "id": "a1", "duration_ms": 5})

    assert display.completed == 1


def test_context_manager_closes_log_file(tmp_path):
    display = make_display(tmp_path, total=1)
    with display:
        _start_dispatch(display)
    assert display._log_file is None
    assert "rest-Foo-bar" in (tmp_path / "run.log").read_text()


def test_log_tail_mirrors_log_lines(tmp_path):
    from doku.progress import LOG_TAIL_LINES

    display = make_display(tmp_path)
    _start_dispatch(display)

    assert any("rest-Foo-bar" in entry.plain for entry in display._log_tail)

    # multi-line panels are collapsed to their title in the tail
    display.on_values(NS, {"messages": [_FakeHumanMessage(PROMPT)]})
    assert any("── prompt → rest-Foo-bar ──" in entry.plain for entry in display._log_tail)
    assert not any("void bar()" in entry.plain for entry in display._log_tail)

    # bounded: only the most recent lines are kept
    for i in range(LOG_TAIL_LINES + 5):
        display.on_update((), "model", {"messages": [_FakeAIMessage(content=f"step {i}")]})
    assert len(display._log_tail) == LOG_TAIL_LINES
    assert any(f"step {LOG_TAIL_LINES + 4}" in entry.plain for entry in display._log_tail)


def test_live_dashboard_on_forced_terminal(tmp_path):
    """Smoke test of the TTY path: Live starts, renders, and stops cleanly."""
    console = Console(file=io.StringIO(), width=120, force_terminal=True)
    display = RunDisplay(console=console, total=2, log_path=tmp_path / "run.log")
    with display:
        _start_dispatch(display)
        display.on_values(NS, {"messages": [_FakeHumanMessage(PROMPT)]})
        display.on_subagent_event({"phase": "complete", "id": "a1", "duration_ms": 100})
    out = console.file.getvalue()
    # "doku" and the tagline are separately styled spans, so match each part
    assert "doku" in out
    assert "— documenting entrypoints" in out
    assert "rest-Foo-bar" in out
    assert "SUBAGENT" in out  # Agent Console table header
    assert "orchestrator" in out  # main agent row
    assert "tail -f" in out  # run-log tail pane
