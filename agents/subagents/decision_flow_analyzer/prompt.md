Analyze the single entrypoint and source supplied in the calling message.
Return only the configured structured response.

Identify every explicit behavioral decision: validation, if/else, switch
branches, null checks, early returns, exception paths, authorization checks,
and feature-toggle branches. Record the source line when it is available.

Produce a `flowchart TD` Mermaid diagram containing entry, decisions as
diamonds, external calls as separate nodes, and every success/error exit.
Follow referenced methods under `/repo` only when necessary. Never invent a
branch or call that is not grounded in source.
