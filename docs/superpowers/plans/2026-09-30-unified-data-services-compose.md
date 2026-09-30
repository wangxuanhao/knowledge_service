# Unified Data Services Docker Compose Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the existing Milvus and Attu deployments to the PostgreSQL Docker Compose project without losing data or recreating PostgreSQL.

**Architecture:** Extend the existing `knowledge-service-postgres` Compose project with two services. Milvus retains its embedded-etcd/local-storage setup and existing host bind mounts; Attu reaches it through Compose DNS at `milvus:19530`. Cutover replaces only the two legacy `docker run` container objects and verifies the existing `knowledge_records` collection before and after restart.

**Tech Stack:** Docker Desktop, Docker Compose, PostgreSQL 18.6, Milvus 3.0.2 standalone, Attu 3.0.0-beta.6, PowerShell

---

### Task 1: Extend the Compose Definition

**Files:**
- Modify: `compose.postgres.yml`

- [ ] **Step 1: Record the pre-cutover invariants**

Run:

```powershell
docker inspect knowledge-postgres --format '{{.Id}}|{{.State.StartedAt}}|{{.State.Health.Status}}|{{json .Mounts}}'
docker inspect milvus-standalone --format '{{.Config.Image}}|{{.State.Health.Status}}|{{json .Mounts}}'
$body = @{dbName='default'} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:19530/v2/vectordb/collections/list' -ContentType 'application/json' -Body $body
```

Expected: PostgreSQL and Milvus are healthy; PostgreSQL uses `knowledge-postgres-data`; Milvus uses `D:\workspace\milvus\volumes\milvus`; the Milvus response includes `knowledge_records`.

- [ ] **Step 2: Add Milvus and Attu services**

Add the following services without changing the existing `name: knowledge-service-postgres`, PostgreSQL service, or `knowledge_postgres_data` volume declaration:

```yaml
  milvus:
    image: milvusdb/milvus:v3.0.2
    container_name: milvus-standalone
    restart: unless-stopped
    security_opt:
      - seccomp:unconfined
    environment:
      DEPLOY_MODE: STANDALONE
      ETCD_USE_EMBED: "true"
      ETCD_DATA_DIR: /var/lib/milvus/etcd
      ETCD_CONFIG_PATH: /milvus/configs/embedEtcd.yaml
      COMMON_STORAGETYPE: local
    command:
      - milvus
      - run
      - standalone
    ports:
      - "19530:19530"
      - "9091:9091"
      - "2379:2379"
    volumes:
      - type: bind
        source: ../milvus/volumes/milvus
        target: /var/lib/milvus
      - type: bind
        source: ../milvus/embedEtcd.yaml
        target: /milvus/configs/embedEtcd.yaml
      - type: bind
        source: ../milvus/user.yaml
        target: /milvus/configs/user.yaml
    healthcheck:
      test:
        - CMD-SHELL
        - curl -f http://localhost:9091/healthz
      interval: 30s
      timeout: 20s
      retries: 3
      start_period: 90s
    stop_grace_period: 60s

  attu:
    image: zilliz/attu:v3.0.0-beta.6
    container_name: attu
    restart: unless-stopped
    environment:
      MILVUS_ADDRESS: milvus:19530
    ports:
      - "3000:3000"
    depends_on:
      milvus:
        condition: service_healthy
```

- [ ] **Step 3: Render the Compose configuration before cutover**

Run:

```powershell
docker compose --env-file .env.postgres -f compose.postgres.yml config --quiet
docker compose --env-file .env.postgres -f compose.postgres.yml config --services
docker compose --env-file .env.postgres -f compose.postgres.yml config --images
```

Expected: exit code 0; services are `postgres`, `milvus`, and `attu`; images match the three fixed tags.

- [ ] **Step 4: Confirm resolved bind-mount sources**

Run:

```powershell
docker compose --env-file .env.postgres -f compose.postgres.yml config
$requiredPaths = @(
  'D:\workspace\milvus\volumes\milvus',
  'D:\workspace\milvus\embedEtcd.yaml',
  'D:\workspace\milvus\user.yaml'
)
$missingPaths = $requiredPaths | Where-Object { -not (Test-Path -LiteralPath $_) }
if ($missingPaths) { throw "Missing Milvus bind sources: $($missingPaths -join ', ')" }
```

Expected: Milvus sources resolve to the three existing paths under `D:\workspace\milvus`; project name remains `knowledge-service-postgres`; PostgreSQL volume name remains `knowledge-postgres-data`.

### Task 2: Safely Transfer Container Ownership to Compose

**Files:**
- No file changes.

- [ ] **Step 1: Save rollback metadata**

Run:

```powershell
New-Item -ItemType Directory -Force -Path '.tmp-compose-cutover' | Out-Null
docker inspect milvus-standalone | Set-Content -LiteralPath '.tmp-compose-cutover\milvus-standalone.inspect.json'
docker inspect attu | Set-Content -LiteralPath '.tmp-compose-cutover\attu.inspect.json'
```

Expected: both inspect snapshots exist locally. They are temporary operational artifacts and must not be committed.

- [ ] **Step 2: Stop the legacy containers**

Run:

```powershell
docker stop attu milvus-standalone
```

Expected: both container names are printed; PostgreSQL remains running and healthy.

- [ ] **Step 3: Remove only the stopped legacy container objects**

Run:

```powershell
docker rm attu milvus-standalone
```

Expected: both container names are printed. Do not use `-v`, remove any volume, or delete anything under `D:\workspace\milvus`.

- [ ] **Step 4: Start only Milvus and Attu through Compose**

Run:

```powershell
docker compose --env-file .env.postgres -f compose.postgres.yml up -d milvus attu
```

Expected: Compose creates `milvus-standalone` and `attu`; it does not recreate `knowledge-postgres`.

### Task 3: Verify Health, Data, and Persistence

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Ignore temporary cutover metadata**

Add:

```gitignore
.tmp-compose-cutover/
```

- [ ] **Step 2: Wait for and inspect service health**

Run:

```powershell
docker compose --env-file .env.postgres -f compose.postgres.yml ps
docker inspect milvus-standalone --format '{{.State.Status}}|{{.State.Health.Status}}|{{.Config.Image}}|{{json .Mounts}}'
docker inspect attu --format '{{.State.Status}}|{{.Config.Image}}|{{range .Config.Env}}{{println .}}{{end}}'
docker inspect knowledge-postgres --format '{{.Id}}|{{.State.StartedAt}}|{{.State.Health.Status}}|{{json .Mounts}}'
docker port milvus-standalone
docker port attu
```

Expected: all containers run; PostgreSQL and Milvus are healthy; Attu has `MILVUS_ADDRESS=milvus:19530`; Milvus uses the original bind mounts; PostgreSQL retains the same ID and start time captured before cutover; ports `19530`, `9091`, `2379`, and `3000` are published.

- [ ] **Step 3: Verify endpoints and existing Milvus data**

Run:

```powershell
(Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:9091/healthz' -TimeoutSec 10).StatusCode
$attuStatus = try { (Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:3000' -TimeoutSec 10).StatusCode } catch { [int]$_.Exception.Response.StatusCode }
$attuStatus
$body = @{dbName='default'} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:19530/v2/vectordb/collections/list' -ContentType 'application/json' -Body $body
```

Expected: Milvus returns 200; Attu returns an HTTP response (its root may return 401); the collection list still includes `knowledge_records`.

- [ ] **Step 4: Verify restart persistence**

Run:

```powershell
docker compose --env-file .env.postgres -f compose.postgres.yml restart milvus attu
docker compose --env-file .env.postgres -f compose.postgres.yml up -d --wait --wait-timeout 180 milvus attu
docker compose --env-file .env.postgres -f compose.postgres.yml ps
$body = @{dbName='default'} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:19530/v2/vectordb/collections/list' -ContentType 'application/json' -Body $body
```

Expected after Milvus becomes healthy again: `knowledge_records` remains; PostgreSQL is still healthy and was not restarted.

- [ ] **Step 5: Review the final diff and commit only project files**

Run:

```powershell
git diff --check
git status --short
git diff -- compose.postgres.yml .gitignore .env.postgres.example
```

Expected: no whitespace errors; `.env.postgres` and `.tmp-compose-cutover` are absent from Git status; the unrelated log file remains untouched.

Commit only the intended infrastructure files:

```powershell
git add -- compose.postgres.yml .gitignore .env.postgres.example
git commit -m "infra: compose postgres milvus and attu"
```

Do not add `.env.postgres`, `.tmp-compose-cutover`, or `data/log/service.log.2026-09-29`.

### Rollback Procedure

If Milvus does not become healthy or the collection check fails:

1. Capture `docker logs milvus-standalone` and `docker logs attu` before removing anything.
2. Run `docker compose --env-file .env.postgres -f compose.postgres.yml rm -sf attu milvus` to remove only the failed Compose containers.
3. Recreate the original containers with the exact known definitions below. Do not invoke the launcher because it rewrites the two Milvus configuration files.
4. Verify the `knowledge_records` collection again.
5. Never remove the Milvus data directory or PostgreSQL named volume.

```powershell
docker run -d `
  --name milvus-standalone `
  --security-opt seccomp=unconfined `
  -e ETCD_USE_EMBED=true `
  -e ETCD_DATA_DIR=/var/lib/milvus/etcd `
  -e ETCD_CONFIG_PATH=/milvus/configs/embedEtcd.yaml `
  -e COMMON_STORAGETYPE=local `
  -e DEPLOY_MODE=STANDALONE `
  -v 'D:\workspace\milvus\volumes\milvus:/var/lib/milvus' `
  -v 'D:\workspace\milvus\embedEtcd.yaml:/milvus/configs/embedEtcd.yaml' `
  -v 'D:\workspace\milvus\user.yaml:/milvus/configs/user.yaml' `
  -p 19530:19530 `
  -p 9091:9091 `
  -p 2379:2379 `
  --health-cmd='curl -f http://localhost:9091/healthz' `
  --health-interval=30s `
  --health-start-period=90s `
  --health-timeout=20s `
  --health-retries=3 `
  milvusdb/milvus:v3.0.2 `
  milvus run standalone

docker run -d `
  --name attu `
  -e MILVUS_ADDRESS=host.docker.internal:19530 `
  -p 3000:3000 `
  zilliz/attu:v3.0.0-beta.6
```
