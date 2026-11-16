# Anzma Platform

This repository contains the executable FastAPI application, PostgreSQL schema and migrations, tests, Docker Compose setup, and project documentation.

## Start the application

1. Copy `deploy/examples/.env.example` to `.env` and set `PGPASSWORD`.
2. From the repository root, run:

   ```powershell
   docker compose -f deploy/compose/docker-compose.yml up --build
   ```

3. Open <http://localhost:8000> for the dashboard or <http://localhost:8000/docs> for the API.

PostgreSQL initializes a new empty data volume from `db/init/anzma_schema_and_seed.sql` and then applies the numbered SQL files mounted by Compose. Existing volumes are not reinitialized automatically.

## Project map

- `app/` — API entry point, routes, domain logic, database and model adapters, and dashboard assets under `app/dashboard/static/`.
- `db/` — initialization schema and versioned migrations.
- `tests/` — unit, integration, and scenario checks.
- `deploy/compose/` — Dockerfile and Compose configuration.
- `docs/` — architecture, data model, run guide, and recorded verification notes.

See [the run and demo guide](docs/RUN_DEMO.md) and [the repository restructure plan](docs/REPOSITORY_RESTRUCTURE_PLAN.md) for details. Runtime verification must be recorded from actual execution; historical test reports are not evidence of a new run.
