Analyze the single entrypoint and source supplied in the calling message.
Return only the configured structured response.

Trace every external-system call reachable from the entrypoint: database and
repository operations, caches, REST clients (`RestTemplate`, `WebClient`,
Feign or wrappers), SOAP clients, and Kafka producers (`KafkaTemplate`,
producer APIs or wrappers). For Kafka producers resolve the topic when
possible. Report the concrete client/bean, operation, destination, usage, and
source line. Follow locally defined wrappers and injected collaborators under
`/repo` as needed. Do not report internal in-process helpers as external and
never infer a dependency without code evidence.
