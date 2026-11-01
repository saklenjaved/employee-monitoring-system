# ADR 0003: In-memory event queue (known gap)

Status: accepted for POC. **Gap against TAD 11** (reliability, recovery from network interruptions).

- The queue is bounded (default 10,000 events; hard max 100,000) and drops the oldest event when full, counting the drops.
- Events are lost if the process exits before they are sent. Retries cover network drops while the process runs.
- Behind the `EventQueue` Protocol, so a SQLite queue can be added once TAD 13 items (approval, encryption, local retention) are settled. See `.claude/rules/sync-and-idempotency.md`.
