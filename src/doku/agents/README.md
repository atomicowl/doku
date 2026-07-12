# Agent configurations

Each agent lives in its own folder with two files:

- `config.toml` — name, description, `response_format` (a model name from
  `doku.models`), filesystem `permissions`, and optional `skills`.
- `prompt.md` — the agent's system prompt.

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
