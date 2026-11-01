# Rule: API contract sync

The API documentation and the code must always match. The Windows agent depends on this contract (`docs/api-contract.yaml`).

- Any change to an endpoint (path, method, request, response, status codes) updates `docs/api-contract.yaml` in the same change.
- Do not rename or remove a field the Windows agent uses without a version plan.
- Each agent endpoint keeps its existing shape. Add new optional fields rather than changing old ones.
- Event payloads follow `docs/event-schema.json`. Any new event type updates that file first.
- Keep error responses consistent: a clear status code and a short message, never a stack trace.

**Why:** A mismatch would silently break agent sync or create security gaps.

**How to apply:** Check the contract before and after an API change. Related: [[sync-and-idempotency]], [[django-module-boundaries]].
