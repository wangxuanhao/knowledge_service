# Unified Environment Configuration Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Compose-only `.env.postgres` file with the project's automatically loaded `.env` while limiting PostgreSQL's container environment to PostgreSQL-specific variables.

**Architecture:** `.env` remains the ignored local source of real values, and `.env.example` documents every supported key without secrets. `compose.yml` uses required `${...}` interpolation in an explicit PostgreSQL `environment` mapping, relying on Compose's default `.env` discovery without injecting unrelated `KG_*` values.

**Tech Stack:** Docker Compose, PostgreSQL 18, dotenv configuration, YAML

---

### Task 1: Document the unified environment contract

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Add the PostgreSQL variables to the template**

Add `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_APP_USER`, `POSTGRES_APP_PASSWORD`, `POSTGRES_INITDB_ARGS`, and `TZ`. Keep both password examples empty.

- [ ] **Step 2: Confirm the template contains no real local secrets**

Compare variable names and presence only; do not print or compare secret values in terminal output.

### Task 2: Make Compose use automatic `.env` discovery

**Files:**
- Modify: `compose.yml:9`

- [ ] **Step 1: Replace the PostgreSQL `env_file` block**

Use an explicit `environment` mapping with required interpolation for all seven PostgreSQL variables. This makes a missing or blank local value fail during configuration rendering.

- [ ] **Step 2: Verify automatic loading before changing local files**

Run `docker compose -f compose.yml config` against a temporary merged environment file named `.env`, with no `--env-file` argument. Expect all four services to render and the PostgreSQL environment to contain no `KG_*` keys.

### Task 3: Merge local values safely

**Files:**
- Modify, ignored: `.env`
- Remove after validation, ignored: `.env.postgres`

- [ ] **Step 1: Merge without exposing values**

Append the PostgreSQL dotenv entries to `.env` mechanically after confirming there are no duplicate keys. Do not print values.

- [ ] **Step 2: Validate key presence and value preservation**

Parse both files locally and assert the seven PostgreSQL values in `.env` exactly match their former `.env.postgres` values. Report only pass/fail.

- [ ] **Step 3: Retire the old file**

Delete `.env.postgres` only after Compose renders successfully from `.env` and the service verification passes.

### Task 4: Reconcile and verify the running stack

**Files:**
- Verify: `compose.yml`

- [ ] **Step 1: Render and inspect the resolved model**

Run `docker compose -f compose.yml config` without `--env-file`. Expect project name `knowledge-service`, four services, and only the seven approved variables in the PostgreSQL environment.

- [ ] **Step 2: Apply the Compose configuration**

Run `docker compose -f compose.yml up -d` and allow Compose to recreate only containers whose configuration changed. Do not remove volumes.

- [ ] **Step 3: Verify health and persistence**

Confirm PostgreSQL, etcd, and Milvus are healthy; Attu is running; PostgreSQL retains its external volume; and Milvus still contains `knowledge_records`.

- [ ] **Step 4: Run tracked-file checks and commit**

Run `git diff --check`, confirm the user's existing log remains untouched, and commit only `compose.yml` and `.env.example` plus plan updates.
