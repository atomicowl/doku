# Agent configurations

Each agent lives in its own folder with two files:

- `config.toml` — name, `role` (subagents only), description,
  `response_format` (a model name from `doku.models`), filesystem
  `permissions`, and optional `skills`.
- `prompt.md` — the agent's system prompt.

## Roles

Every subagent declares a `role`, and the orchestrator's run flow is derived
from the folders on disk — no code or prompt edits needed to add an agent:

- `role = "discoverer"` — dispatched in parallel in phase 1. Returns
  `DiscoveredItems`: a list of `{kind, name, file, line, meta}` items. The
  pipeline is task-agnostic — items can be REST endpoints, Kafka consumers,
  cron jobs, feature flags, anything worth documenting.
- `role = "documenter"` — exactly one; dispatched once per discovered item in
  phase 2 with the item's source inlined.

The orchestrator's `prompt.md` is a template: `__DISCOVERERS_LIST__`,
`__DISCOVERERS_JS__`, `__DOCUMENTER__`, and `__CONCURRENCY__` are filled at
build time from the loaded subagents (see `doku.agent`).

## Skills

Any agent (main or subagent) can declare skill sources in its `config.toml`:

```toml
skills = ["skills"]
```

Each entry is a directory relative to the agent's folder; each subdirectory of
it is one [Anthropic-style skill](https://docs.langchain.com/oss/python/deepagents/skills)
— a folder named after the skill containing a `SKILL.md` with YAML frontmatter
(`name`, `description`) followed by the instructions:

```
orchestrator/
  config.toml        # skills = ["skills"]
  skills/
    my-skill/
      SKILL.md
      helper.py      # optional supporting files
```

At build time each source is mounted read-only into the agent's virtual
filesystem at `/skills/<agent-name>/<dir>` and wired into deepagents'
`SkillsMiddleware`: the agent sees name + description of every skill in its
system prompt and reads the full `SKILL.md` on demand. Read access to the
mount is granted automatically, ahead of any deny rules in `permissions`.

Layout:

- `orchestrator/` — the main agent. Its prompt may use the `__CONCURRENCY__`
  placeholder, substituted at build time from `--concurrency`.
- `subagents/<name>/` — one folder per subagent. `doku.agent` discovers them
  automatically: add a new subagent by dropping in a folder with a
  `config.toml` and `prompt.md`.
