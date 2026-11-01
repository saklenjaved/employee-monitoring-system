# Rule: Django module boundaries

The backend is one Django project with clear internal apps (TAD 3). It is not a set of microservices.

Apps and their jobs:
- `accounts`, `organizations`, `access_control`, `workforce`, `devices`, `monitoring`, `screenshots`, `productivity`, `attendance`, `analytics`, `reports`, `notifications`, `audit`, `integrations`.

Rules:
- Each app owns its own models. Do not write into another app's tables directly.
- Apps talk to each other through small service functions (for example `audit.services.record_event`).
- Views and API handlers stay short. Business logic lives in `services.py`.
- Long jobs (summaries, cleanup, reports) run as Django management commands, not inside web requests (TAD 9).
- Keep imports one way where possible. `analytics` may read from `monitoring`, but `monitoring` should not import `analytics`.

**Why:** Clear boundaries keep the code readable for a beginner and easier to change safely.

**How to apply:** Use the `django-app-module` skill to create a new app. Related: [[beginner-friendly-style]], [[api-contract-sync]].
