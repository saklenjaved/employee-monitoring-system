# Rule: Beginner-friendly code style

The code must be simple and easy to read, even for a beginner.

- Prefer plain functions and simple classes. Avoid advanced patterns, metaclasses and deep inheritance.
- Use clear names. `employee_list` is better than `emp_lst`.
- Keep functions short. If a function does more than one thing, split it.
- Add a short comment only where the reason is not obvious. Do not comment every line.
- Do not add an abstraction until it is needed twice.
- Follow Django's own conventions (models, views, serializers, urls) instead of inventing new structures.
- Keep dependencies to what the TAD names. Ask before adding a new library.

**Why:** Simple code is easier to review, safer to change and easier to hand over.

**How to apply:** Match the surrounding code. Related: [[django-module-boundaries]].
