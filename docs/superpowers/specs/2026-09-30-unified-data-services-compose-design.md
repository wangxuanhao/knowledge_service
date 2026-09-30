# Unified Data Services Docker Compose Design

## Goal

Manage PostgreSQL, external etcd, Milvus, and Attu as the `knowledge-service` Docker Compose project while preserving the existing PostgreSQL volume and Milvus data.

## Current Target Architecture

The target deployment has four services in `compose.yml`:

- `postgres` runs `postgres:18.6-bookworm` as `knowledge-postgres` and keeps the physical named volume `knowledge-postgres-data` unchanged.
- `etcd` runs `quay.io/coreos/etcd:v3.5.33` as `knowledge-etcd`. It is the only writer to the existing etcd data directory.
- `milvus` runs `milvusdb/milvus:v3.0.2` as `milvus-standalone` in standalone mode with local storage and connects to `etcd:2379`.
- `attu` runs `zilliz/attu:v3.0.0-beta.6` as `attu` and connects to `milvus:19530`.

Milvus's former embedded-etcd configuration is retained only as an emergency rollback definition. It is not part of the target deployment.

## Service and Health Dependency Chain

All four services join the Compose project's default bridge network. The startup and health chain is:

```text
etcd healthy -> Milvus healthy -> Attu starts
```

Milvus uses long-form `depends_on` with `condition: service_healthy` and `restart: true` for etcd. Attu uses `condition: service_healthy` for Milvus. PostgreSQL is independent of this chain.

Host interfaces are:

- PostgreSQL: `127.0.0.1:5432`
- etcd: `127.0.0.1:2379` only; containers use `etcd:2379`
- Milvus: `19530` and `9091`
- Attu: `3000`

The etcd process uses a 4 GiB backend quota and revision-based auto-compaction with a 1,000-revision retention window. Its health check calls `etcdctl endpoint health` against the container-local client endpoint.

## Data Ownership

Data ownership is explicit so only one process can write the etcd store:

- PostgreSQL exclusively owns the physical Docker volume `knowledge-postgres-data` mounted at `/var/lib/postgresql`.
- External etcd exclusively owns `../milvus/volumes/milvus/etcd`, mounted read-write at `/etcd-data`.
- Milvus keeps the writable parent bind `../milvus/volumes/milvus` at `/var/lib/milvus` for local Milvus data.
- A more-specific bind shadows `/var/lib/milvus/etcd` inside the Milvus container and mounts the same host etcd directory read-only. This prevents Milvus from becoming a second writer even though the parent mount is writable.
- Milvus reads `../milvus/user.yaml` at `/milvus/configs/user.yaml`.

Every bind uses `bind.create_host_path: false`; missing sources fail validation instead of silently creating empty paths.

## Environment Configuration

The project uses `.env` as the single local source for application and infrastructure configuration. Docker Compose automatically reads this file from the project directory for `${VARIABLE}` interpolation, so normal commands do not require `--env-file`.

PostgreSQL receives only its required `POSTGRES_*` and `TZ` values through the service's explicit `environment` mapping. The complete `.env` file is not injected into the container, which prevents unrelated application secrets such as LLM API keys from being exposed there. `.env.example` mirrors the supported variable names with non-secret examples and remains safe to commit. The former `.env.postgres` file is retired after its values are merged into `.env`.

## Cutover and Snapshot Safety

Before any container changes, render `compose.yml` with project name `knowledge-service`, record the PostgreSQL identity and volume, record the existing Milvus collection list, and save inspect metadata for the legacy Milvus and Attu containers.

The cutover order is deliberately staged:

1. Stop and remove only the legacy Attu and Milvus container objects.
2. Start external etcd by itself against the existing etcd data directory.
3. Wait for external etcd to become healthy.
4. Create and copy out a real etcd snapshot while Milvus is still stopped.
5. Start Milvus and wait for it to become healthy.
6. Verify the existing `knowledge_records` collection, then start Attu.
7. Confirm PostgreSQL retained its original container identity, start time, and physical named volume.

The snapshot boundary matters: etcd must be healthy enough to produce a consistent snapshot, while Milvus must not yet be running and changing metadata.

## Failure Handling and Rollback

If cutover validation fails, capture logs and preserve the etcd snapshot before changing containers. Stop and remove Compose-managed Attu and Milvus, then stop and remove external etcd. Confirm `knowledge-etcd` is absent before starting any embedded-etcd Milvus rollback container. This ordering preserves the single-writer invariant.

The embedded-etcd `docker run` definition in the implementation plan is rollback-only. It must never run concurrently with external etcd because both use `../milvus/volumes/milvus/etcd`. Do not remove or recursively modify the Milvus data directory, do not remove `knowledge-postgres-data`, and do not use Docker volume pruning.

## Validation

The migration is accepted only when all of the following checks pass:

- `docker compose -f compose.yml config` automatically loads `.env` and renders exactly `postgres`, `etcd`, `milvus`, and `attu` with the pinned images.
- The rendered PostgreSQL environment contains the required PostgreSQL variables but none of the application's `KG_*` variables.
- Port 2379 is published only on `127.0.0.1`; Milvus publishes only 19530 and 9091.
- External etcd has the configured quota, compaction settings, writable data mount, and healthy endpoint.
- The Milvus etcd shadow mount renders read-only, Milvus points to `etcd:2379`, and the health dependency chain is present.
- PostgreSQL remains healthy with the same container identity, start time, and physical volume `knowledge-postgres-data`.
- The pre-Milvus etcd snapshot exists outside the container and reports valid snapshot metadata.
- The `knowledge_records` collection remains present after cutover and after the staged restart checks for etcd, Milvus, and Attu.

## Out of Scope

- Migrating application persistence from SQLite to PostgreSQL.
- Changing Milvus standalone mode to a distributed cluster.
- Upgrading PostgreSQL, Milvus, Attu, or etcd beyond the pinned versions.
- Changing application connection strings or adding authentication in front of Attu.
