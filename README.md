# doku

Task-agnostic AI agent workflow harness. Its bundled `document-codebase`
workflow scans a codebase, finds its entrypoints (REST APIs, SOAP
APIs, Kafka consumers), and generates documentation for each one: input/output
models, feature toggles, a flow graph (decision points, external calls), and the external
dependencies it touches (databases, caches, REST/SOAP clients, Kafka producers).

The run is agentic end to end: entrypoint *discovery* is performed by three
specialized discovery subagents dispatched in parallel — one each for REST
APIs, SOAP APIs, and Kafka consumers in Java/Kotlin codebases — whose merged,
deduplicated output becomes the candidate manifest. For every candidate, one
call-chain specialist traces reachable code and analyzes decision flow, feature
toggles, and external dependencies only in that entrypoint's context. Its
structured findings and the source are then
synthesized by the documenter subagent in parallel batches using
[deepagents](https://github.com/langchain-ai/deepagents)' code-interpreter
dispatch loop.

## Setup

```bash
uv sync
cp .env.example .env   # then fill in DOKU_API_KEY, DOKU_API_BASE, DOKU_MODEL
```

Configuration is read from a `.env` file (searched from the working directory
upward; see `.env.example`) and/or plain environment variables — real
environment variables win over `.env` entries. All three settings are
required — there are no built-in defaults, and doku exits with an error naming
whatever is missing (the model may alternatively be passed as `--model`).
`.env` is gitignored so keys never land in the repo. Models are addressed via [OpenRouter](https://openrouter.ai)
(model ids like `openrouter:z-ai/glm-5.2`); any other provider supported by
`langchain.chat_models.init_chat_model` (e.g. `anthropic:...`) also works —
`DOKU_API_KEY`/`DOKU_API_BASE` are passed to whichever provider the model id
selects (provider-specific env vars like `OPENROUTER_API_KEY` are not
consulted).

## Usage

```bash
uv run doku /path/to/target-repo --out ./docs
```

Select a bundled workflow by name or load one directly from a directory:

```bash
uv run doku /path/to/repo --workflow document-codebase
uv run doku /path/to/repo --workflow ./workflows/my_review \
  --agents-dir ./agents
```

A workflow folder contains `config.toml` and `prompt.md`. Its config explicitly
allowlists the subagents it may spawn and may declare a local Pydantic response
model and finalizer. Create and validate external workflows without rebuilding
the harness:

```bash
uv run doku-workflow create my-review
uv run doku-workflow validate workflows/my_review --agents-dir ./agents
```

`agents/main/prompt.md` is an optional global main-agent system prompt.
When non-empty, the harness prepends it to the selected workflow's prompt.
The default definition roots are `./agents` and `./workflows` in the current
working directory. Configure them with `--agents-dir` / `DOKU_AGENTS_DIR` and
`--workflows-dir` / `DOKU_WORKFLOWS_DIR`. A value passed to `--workflow` is
first accepted as a directory path; otherwise it is resolved by name under
the configured workflows directory (both `my-workflow` and `my_workflow`
folder spellings are supported).

Options:

- `--model` — LLM model id, e.g. `openrouter:z-ai/glm-5.2`. Required, no
  default: pass the flag or set `DOKU_MODEL` (the flag wins if both are
  given).
- `--concurrency` — max entrypoints documented in parallel, default `5`.
- `--chat-completions` — with `openai:*` models, talk the plain Chat
  Completions API instead of the OpenAI Responses API. Also settable via
  `DOKU_CHAT_COMPLETIONS=1`.

Environment variables (all required, settable in `.env`): `DOKU_API_KEY`
(LLM provider API key), `DOKU_API_BASE` (provider base URL), `DOKU_MODEL`
(model id, unless `--model` is passed). Optional: `DOKU_CHAT_COMPLETIONS`;
`DOKU_MODEL_RPS` / `DOKU_MODEL_BURST` — rate-limit LLM requests to at
most RPS request starts per second with bursts up to BURST (default `1`);
the limit is shared by the orchestrator and all parallel subagents, so it
caps the whole run regardless of `--concurrency`, and unset means no limit;
`DOKU_MODEL_MAX_RETRIES` — retries per request inside the provider client
(`0` disables retrying, unset keeps the provider's default).

### Custom OpenAI-compatible providers

Any server that speaks the OpenAI Chat Completions API (vLLM, Ollama,
LiteLLM, corporate gateways) works via the `openai:` model prefix plus
`--chat-completions` (without it, requests go to the OpenAI *Responses* API,
which most compatible servers don't implement):

```bash
DOKU_MODEL=openai:llama-3.3-70b
DOKU_API_BASE=http://localhost:8000/v1
DOKU_API_KEY=dummy            # must be non-empty even if your server ignores auth
DOKU_CHAT_COMPLETIONS=1
```

The served model must support tool/function calling — the orchestrator's
dispatch loop depends on it.

While it runs, the terminal shows a live, self-updating
[Rich](https://github.com/Textualize/rich) dashboard — overall progress, what
the orchestrator is currently doing, every subagent currently running (with
elapsed time and its latest activity), and recent finishes (the dispatch loop
otherwise runs inside one opaque tool call with no output until everything is
done):

The dashboard follows the Agent Console design (terminal-green on black,
IBM-Plex-Mono-style layout):

```
➜ doku — documenting entrypoints
tokens 97.9k  ·  elapsed 1m23s  ·  2 running  ·  1 done  ·  1 error

 SUBAGENT                         STATUS    TOKENS   TIME    TASK
 ─────────────────────────────────────────────────────────────────────────────
 orchestrator                     running   12.7k    1m23s   running dispatch loop (eval, 41 lines of JS)…

 rest-OwnerController-showOwner   running   42.1k    5s      → read_file (2 calls)
 kafka-OrderEvents-onMessage      running   31.5k    2s      starting…
 soap-BillingEndpoint-getInvoice  done      18.4k    4s      finished — 2 tool call(s)
 rest-VetController-showVetList   error     9.8k     2s      timeout

───────────────────────────────────────────────────────────────────────────────
➜ full log: docs/_state/run.log ▊
```

The full play-by-play is written to `docs/_state/run.log` (tail -f-able while
the run is live): the orchestrator's reasoning, the dispatch-loop JavaScript
it writes (this code *is* the dynamic subagent flow), and, per subagent, the
exact prompt it received (with the inlined source collapsed to a note) plus
every tool call and result:

```
  orchestrator │ Discovery found 2 candidates. Running the dispatch loop now.
╭─ orchestrator → eval · dispatch loop (41 lines) ─────────────────────────╮
│    1 function stripLineNumbers(text) {                                   │
│    ⋮                                                                     │
╰──────────────────────────────────────────────────────────────────────────╯
  ▶ [1]    rest-OwnerController-showOwner
╭─ prompt → rest-OwnerController-showOwner ────────────────────────────────╮
│ Document this REST entrypoint: class OwnerController, method showOwner,  │
│ file /repo/src/main/java/.../OwnerController.java around line 93. ...    │
│                                                                          │
│ [+ full source of OwnerController.java inlined — 214 lines]              │
╰──────────────────────────────────────────────────────────────────────────╯
    rest-OwnerController-showOwner │ The handler loads the owner by id...
    rest-OwnerController-showOwner → read_file {"file_path": "/repo/src/..."}
    rest-OwnerController-showOwner ← read_file: 1 package org.springframework...
  ✓ rest-OwnerController-showOwner (5.1s, 2 tool call(s))
```

When stdout is not a TTY (piped to a file or CI), the dashboard is skipped
and plain one-line lifecycle messages are printed instead.

Output (`./docs`):

```
docs/
  index.md            # table of all discovered entrypoints
  dependencies.md      # aggregated external dependencies, cross-referenced to entrypoints
  entrypoints/
    rest-OrderController-createOrder.md
    kafka-OrderEventsConsumer-onMessage.md
    soap-BillingEndpoint-getInvoice.md
  _state/entrypoints.json   # candidate manifest, written by the orchestrator after discovery
  _state/results/*.json     # one structured result per entrypoint, straight from the subagent
  _errors.md            # only written if some entrypoints failed to document
```

## v1 scope

The shipped discovery subagents cover Java/Kotlin: Spring MVC/WebFlux and
JAX-RS (REST), Spring-WS and JAX-WS/CXF (SOAP), `@KafkaListener`/
`@KafkaHandler` plus manual consumer poll loops (Kafka). The pipeline itself
is task-agnostic: each subagent lives in its own folder under
`agents/subagents/` with a `role` (`discoverer` or `documenter`) in
its `config.toml`, and the orchestrator's flow is derived from the folders on
disk. Dropping in a new discoverer folder (say, for scheduled jobs or gRPC
services) is the whole job — it gets dispatched automatically, no code or
prompt edits.

The earlier deterministic tree-sitter detectors remain in
`src/doku/detectors/` but are no longer wired into the CLI.

## Development

```bash
uv run pytest
```
