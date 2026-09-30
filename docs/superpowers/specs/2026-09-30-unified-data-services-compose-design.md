# Unified Data Services Docker Compose Design

## Goal

Manage PostgreSQL, Milvus, and Attu through the existing Docker Compose project while preserving the running PostgreSQL instance and all existing Milvus data.

## Current State

- PostgreSQL runs as `knowledge-postgres` from `postgres:18.6-bookworm` and is already managed by `compose.postgres.yml` under the `knowledge-service-postgres` Compose project.
- Milvus runs as `milvus-standalone` from `milvusdb/milvus:v3.0.2`. It uses embedded etcd and local object storage.
- Milvus persists data in the host directory `D:\workspace\milvus\volumes\milvus` and reads `embedEtcd.yaml` and `user.yaml` from `D:\workspace\milvus`.
- Attu runs as `attu` from `zilliz/attu:v3.0.0-beta.6` and currently reaches Milvus through `host.docker.internal:19530`.
- Milvus and Attu were created with `docker run`, so Docker Compose cannot adopt their existing container objects directly.

## Chosen Approach

Extend `compose.postgres.yml` with `milvus` and `attu` services while keeping the existing Compose project name and PostgreSQL service definition. Preserve the exact Milvus and Attu image tags requested by the user.

Milvus will use bind mounts that resolve to the existing configuration and data paths. The service will retain embedded etcd, local storage, the original command, the original health check, and the existing host ports. Its container will run with the original unconfined seccomp setting because the vendor-provided Windows standalone launcher used that setting.

Attu will connect to `milvus:19530` over the Compose network. It will depend on the Milvus health check and retain port 3000. Both services will use `restart: unless-stopped` so they recover after Docker or host restarts.

## Services and Networking

All three services will join the Compose project's default bridge network. Container-to-container traffic will use Compose DNS names:

- Application or administrative clients may reach PostgreSQL on `127.0.0.1:5432`.
- Milvus remains available on host ports `19530`, `9091`, and `2379` to preserve existing integrations.
- Attu remains available on host port `3000` and reaches Milvus internally at `milvus:19530`.

The public port shape is intentionally unchanged in this migration. Restricting Milvus ports to localhost is a separate hardening change because changing it now could break unknown clients.

## Data Safety and Cutover

Before cutover, capture the current container configuration and verify that Milvus is healthy and the data directory exists. Render and validate the merged Compose configuration before stopping anything.

For cutover:

1. Stop the existing `attu` and `milvus-standalone` containers.
2. Remove only those two container objects so their names can be reused by Compose.
3. Do not remove or modify `D:\workspace\milvus\volumes\milvus`, `embedEtcd.yaml`, or `user.yaml`.
4. Start the Compose-managed Milvus and Attu services.
5. Leave the existing PostgreSQL container running and under its current Compose project identity.

The expected service interruption is limited to Milvus and Attu startup time.

## Failure Handling and Rollback

If the Compose-managed Milvus fails validation, stop and remove only the new Milvus and Attu container objects. Recreate them with the previously captured image tags, environment, mounts, commands, ports, security option, and health check. Because both old and new Milvus instances use the same host data directory, container replacement does not move or delete persistent data.

No command in the migration may use Docker volume pruning, recursive filesystem deletion, or the `delete` action from the original Milvus launcher.

## Validation

The migration is accepted only when all of the following checks pass:

- `docker compose config` renders successfully and resolves the three services.
- PostgreSQL remains healthy and retains its existing named volume.
- Milvus reaches Docker health status `healthy` and `http://127.0.0.1:9091/healthz` returns HTTP 200.
- Attu responds on port 3000 and its environment points to `milvus:19530`.
- Milvus uses the original host data directory and configuration files.
- The three expected image tags and host ports are present.
- Restarting the Compose services preserves PostgreSQL and Milvus data and returns all services to their expected state.

## Out of Scope

- Migrating application persistence from SQLite to PostgreSQL.
- Changing the Milvus deployment from standalone mode to a distributed cluster.
- Upgrading PostgreSQL, Milvus, or Attu beyond the versions already selected.
- Changing application connection strings or adding authentication in front of Attu.
