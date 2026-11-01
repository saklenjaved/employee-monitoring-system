---
name: django-app-module
description: Create a new Django feature app in the modular monolith with the standard layout (models, services, serializers, views, urls, tests). Use when adding a new app from TAD section 3.
---

# Django app module

Use this to create a new app. Follow [[django-module-boundaries]] and [[beginner-friendly-style]].

1. **Check the TAD.** Confirm the app name is in TAD section 3. If it is not, ask before creating it.
2. **Create the app.** Use `python manage.py startapp <name>` inside the backend package.
3. **Add the standard files.**
   - `models.py`: organization-owned models have an `organization` field.
   - `services.py`: the business logic, as plain functions.
   - `serializers.py`: input and output validation for the API.
   - `views.py`: short handlers that call services.
   - `urls.py`: the routes, included from the project URLs.
   - `permissions.py`: the role checks, using [[rbac-permissions]].
   - `tests/`: tests for services and views.
4. **Register the app** in the project settings.
5. **Migrate.** Create the migration and check that it applies on an empty database.
6. **Add the first tests.** Include the tenant test from the `tenant-isolation-tests` skill.

## Checks before finishing
- Models, services and views each have one clear job.
- Nothing imports from an app that depends on it (see [[django-module-boundaries]]).
- The app is in the TAD section 3 list.
