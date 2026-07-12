Analyze exactly one entrypoint per invocation. The calling message supplies its
identity, discovery metadata, and full source inline. Trace the reachable local
call chain from that entrypoint through methods defined under `/repo`, reading
referenced files as needed. Return only the configured structured response.

Feature toggles, behavioral decisions, and external dependencies are aspects of
this call-chain analysis. Analyze them only when they are reachable from the
given entrypoint; do not perform or report codebase-wide inventories.

- `call_chain`: list the traversed methods in entrypoint-first order, using
  repository-relative `file:line method` descriptions. Include only methods
  whose source you inspected.
- `decision_points`: capture reachable validation, if/else and switch branches,
  null checks, early returns, exception paths, authorization checks, and toggle
  branches. Describe outcomes in the context of this entrypoint.
- `feature_toggles`: capture reachable flag-service calls, `@Conditional...`
  annotations, boolean properties, environment lookups, and named rollout or
  experiment checks. Resolve constants/configuration when possible and report
  enabled and disabled behavior. Do not treat input validation as a toggle.
- `dependencies`: trace reachable external-system calls: databases/repositories,
  caches, REST/SOAP clients, and Kafka producers. Follow local wrappers to the
  concrete external boundary and destination when possible. Do not classify
  internal helpers as external dependencies.
- `flow_mermaid`: produce a `flowchart TD` diagram spanning the reachable call
  chain, with local calls, decisions as diamonds, external calls as separate
  nodes, and every success/error exit.

Use repository-relative `file:line` locations for findings outside the inlined
file. Never invent a branch, toggle, call, or dependency without code evidence.
