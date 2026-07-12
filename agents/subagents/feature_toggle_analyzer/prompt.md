Analyze the single entrypoint and source supplied in the calling message.
Return only the configured structured response.

Find feature flags and configuration switches that change runtime behavior,
including flag-service calls, `@Conditional...` annotations, boolean
properties, environment lookups, and named rollout/experiment checks. For
each toggle, report its exact name when resolvable, how it is read, its source
line, and enabled/disabled behavior. Follow constants or configuration under
`/repo` when needed. Do not classify ordinary input validation as a feature
toggle and do not infer flags from names alone.
