# Rule: Event sync, retries and no duplicates

- Send events to the DRF ingestion endpoint in bounded batches over HTTPS (TAD 6, 8). Set a maximum batch size and maximum payload size in configuration.
- Every event has a unique event ID, created on the agent when the event is made. The server uses it to ignore duplicates (TAD 11, idempotency).
- Retry failed sends with exponential backoff and a cap. Retrying the same batch must never create duplicate records.
- Do not write an event to a local queue and also send it live without a clear rule that prevents double-counting.
- The local offline queue (TAD 5.2, SQLite option) is **off by default**. Enable it only after the open TAD 13 items on offline buffering, encryption and local retention are approved. When enabled, it must be bounded in size and age.
- Sync and screenshot upload are separate flows (TAD 9). A failed screenshot upload must not block event sync.
- Record the last successful sync time (TAD 5.1, logging and health).

**Why:** Network drops are common. Without unique IDs and bounded retries, employees' activity is either lost or counted twice.

**How to apply:** Check that every new event type includes an event ID. Test retries against a fake server that drops connections. Related: [[screenshot-policy]], [[privacy-no-keystrokes]].
