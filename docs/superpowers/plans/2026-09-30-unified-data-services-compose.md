# Unified Data Services Docker Compose Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents are available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut over the existing Milvus and Attu deployment to the four-service `knowledge-service` Compose project with external etcd, without losing Milvus data or recreating PostgreSQL.

**Architecture:** `compose.yml` defines PostgreSQL, external etcd 3.5.33, Milvus 3.0.2 standalone, and Attu. External etcd is the only writer to the existing etcd directory. Milvus keeps its writable parent data bind but receives a read-only shadow bind over `/var/lib/milvus/etcd`. The health chain is etcd, then Milvus, then Attu.

**Tech Stack:** Docker Desktop, Docker Compose v2.38.1 or newer, PostgreSQL 18.6, etcd 3.5.33, Milvus 3.0.2 standalone, Attu 3.0.0-beta.6, PowerShell

---

### Task 1: Validate the Four-Service Definition

**Files:**
- Modify: `compose.yml`

- [ ] **Step 1: Record pre-cutover invariants and rollback metadata**

Choose a durable backup directory outside the repository and record the currently running containers before stopping anything:

```powershell
$cutoverDir = 'D:\workspace\backups\knowledge-service\2026-09-30-etcd-cutover'
New-Item -ItemType Directory -Force -Path $cutoverDir | Out-Null

docker inspect knowledge-postgres --format '{{.Id}}|{{.State.StartedAt}}|{{.State.Health.Status}}|{{json .Mounts}}' |
  Set-Content -LiteralPath (Join-Path $cutoverDir 'postgres.before.txt')
docker inspect milvus-standalone | Set-Content -LiteralPath (Join-Path $cutoverDir 'milvus-standalone.inspect.json')
docker inspect attu | Set-Content -LiteralPath (Join-Path $cutoverDir 'attu.inspect.json')

$body = @{dbName='default'} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:19530/v2/vectordb/collections/list' -ContentType 'application/json' -Body $body |
  ConvertTo-Json -Depth 20 |
  Set-Content -LiteralPath (Join-Path $cutoverDir 'collections.before.json')
```

Expected: PostgreSQL and Milvus are healthy; PostgreSQL uses `knowledge-postgres-data`; the Milvus response includes `knowledge_records`; all four evidence files exist outside Git.

- [ ] **Step 2: Render the target configuration**

Run every Compose operation with the explicit project name and file:

```powershell
docker compose --project-name knowledge-service --env-file .env.postgres -f compose.yml config --quiet
docker compose --project-name knowledge-service --env-file .env.postgres -f compose.yml config --services
docker compose --project-name knowledge-service --env-file .env.postgres -f compose.yml config --images
docker compose --project-name knowledge-service --env-file .env.postgres -f compose.yml config
```

Expected: exit code 0; project name is `knowledge-service`; services are `postgres`, `etcd`, `milvus`, and `attu`; images are the four pinned tags.

- [ ] **Step 3: Confirm hardening and resolved data ownership**

```powershell
$requiredPaths = @(
  'D:\workspace\milvus\volumes\milvus',
  'D:\workspace\milvus\volumes\milvus\etcd',
  'D:\workspace\milvus\user.yaml'
)
$missingPaths = $requiredPaths | Where-Object { -not (Test-Path -LiteralPath $_) }
if ($missingPaths) { throw "Missing bind sources: $($missingPaths -join ', ')" }

$json = docker compose --project-name knowledge-service --env-file .env.postgres -f compose.yml config --format json
$config = $json | ConvertFrom-Json
$shadow = $config.services.milvus.volumes | Where-Object target -eq '/var/lib/milvus/etcd'

if ($config.services.etcd.ports[0].host_ip -ne '127.0.0.1') { throw 'etcd port 2379 is not localhost-only' }
if (-not $shadow.read_only) { throw 'Milvus etcd shadow mount is not read-only' }
if ($config.services.milvus.depends_on.etcd.condition -ne 'service_healthy') { throw 'Milvus does not wait for healthy etcd' }
if (-not $config.services.milvus.depends_on.etcd.restart) { throw 'Milvus dependency restart propagation is disabled' }
if ($config.volumes.knowledge_postgres_data.name -ne 'knowledge-postgres-data') { throw 'PostgreSQL physical volume changed' }
```

Also confirm the rendered etcd command contains:

```text
--quota-backend-bytes=4294967296
--auto-compaction-mode=revision
--auto-compaction-retention=1000
```

Expected: etcd owns the writable `/etcd-data` mount, the more-specific Milvus submount is read-only, port 2379 binds only to localhost, and PostgreSQL's physical volume is unchanged.

### Task 2: Cut Over to External etcd

**Files:**
- No file changes.

- [ ] **Step 1: Stop and remove only the legacy application containers**

```powershell
docker stop attu milvus-standalone
docker rm attu milvus-standalone
```

Expected: only the legacy Attu and Milvus container objects are removed. Do not use `-v`; do not stop or recreate PostgreSQL; do not modify anything under `D:\workspace\milvus`.

- [ ] **Step 2: Start external etcd by itself and wait for health**

```powershell
docker compose --project-name knowledge-service --env-file .env.postgres -f compose.yml up -d --wait --wait-timeout 120 etcd
docker compose --project-name knowledge-service --env-file .env.postgres -f compose.yml ps etcd
docker compose --project-name knowledge-service --env-file .env.postgres -f compose.yml exec -T etcd `
  /usr/local/bin/etcdctl --endpoints=http://127.0.0.1:2379 endpoint health
```

Expected: `knowledge-etcd` is healthy and owns the existing etcd directory. Milvus and Attu are still stopped and absent.

- [ ] **Step 3: Create a real etcd snapshot before Milvus starts**

Do not start Milvus until every command in this step succeeds:

```powershell
docker compose --project-name knowledge-service --env-file .env.postgres -f compose.yml exec -T etcd `
  /usr/local/bin/etcdctl --endpoints=http://127.0.0.1:2379 snapshot save /tmp/pre-milvus-cutover.db
docker compose --project-name knowledge-service --env-file .env.postgres -f compose.yml exec -T etcd `
  /usr/local/bin/etcdutl snapshot status /tmp/pre-milvus-cutover.db --write-out=table
docker cp knowledge-etcd:/tmp/pre-milvus-cutover.db (Join-Path $cutoverDir 'pre-milvus-cutover.db')

if (-not (Test-Path -LiteralPath (Join-Path $cutoverDir 'pre-milvus-cutover.db'))) {
  throw 'The pre-Milvus etcd snapshot was not copied out of the container'
}
```

Expected: snapshot creation and status validation succeed, and a non-container copy exists in `$cutoverDir`. This is the recovery boundary captured after external etcd became healthy and before Milvus could write metadata.

- [ ] **Step 4: Start and validate Milvus before starting Attu**

```powershell
docker compose --project-name knowledge-service --env-file .env.postgres -f compose.yml up -d --wait --wait-timeout 180 milvus
docker compose --project-name knowledge-service --env-file .env.postgres -f compose.yml ps etcd milvus

(Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:9091/healthz' -TimeoutSec 10).StatusCode
$body = @{dbName='default'} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:19530/v2/vectordb/collections/list' -ContentType 'application/json' -Body $body
```

Expected: etcd and Milvus are healthy; the health endpoint returns 200; `knowledge_records` is present.

- [ ] **Step 5: Start Attu and validate the complete chain**

```powershell
docker compose --project-name knowledge-service --env-file .env.postgres -f compose.yml up -d attu
docker compose --project-name knowledge-service --env-file .env.postgres -f compose.yml ps

$attuStatus = try {
  (Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:3000' -TimeoutSec 10).StatusCode
} catch {
  [int]$_.Exception.Response.StatusCode
}
$attuStatus

docker inspect attu --format '{{range .Config.Env}}{{println .}}{{end}}'
docker inspect knowledge-postgres --format '{{.Id}}|{{.State.StartedAt}}|{{.State.Health.Status}}|{{json .Mounts}}'
```

Expected: all four services run; etcd, Milvus, and PostgreSQL are healthy; Attu returns an HTTP response and has `MILVUS_ADDRESS=milvus:19530`.

Compare PostgreSQL with the saved invariant:

```powershell
$postgresBefore = (Get-Content -Raw -LiteralPath (Join-Path $cutoverDir 'postgres.before.txt')).Trim()
$postgresAfter = (docker inspect knowledge-postgres --format '{{.Id}}|{{.State.StartedAt}}|{{.State.Health.Status}}|{{json .Mounts}}').Trim()
if ($postgresAfter -ne $postgresBefore) { throw 'PostgreSQL identity, start time, health, or mounts changed' }
```

### Task 3: Staged Restart and Persistence Validation

**Files:**
- No file changes.

- [ ] **Step 1: Restart and test external etcd**

```powershell
docker compose --project-name knowledge-service --env-file .env.postgres -f compose.yml restart etcd
docker compose --project-name knowledge-service --env-file .env.postgres -f compose.yml up -d --wait --wait-timeout 180 etcd milvus
docker compose --project-name knowledge-service --env-file .env.postgres -f compose.yml exec -T etcd `
  /usr/local/bin/etcdctl --endpoints=http://127.0.0.1:2379 endpoint health

$body = @{dbName='default'} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:19530/v2/vectordb/collections/list' -ContentType 'application/json' -Body $body
```

Expected: etcd returns healthy, Milvus returns healthy after dependency recovery, and `knowledge_records` remains present.

- [ ] **Step 2: Restart and test Milvus**

```powershell
docker compose --project-name knowledge-service --env-file .env.postgres -f compose.yml restart milvus
docker compose --project-name knowledge-service --env-file .env.postgres -f compose.yml up -d --wait --wait-timeout 180 milvus
(Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:9091/healthz' -TimeoutSec 10).StatusCode

$body = @{dbName='default'} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:19530/v2/vectordb/collections/list' -ContentType 'application/json' -Body $body
```

Expected: Milvus becomes healthy and `knowledge_records` remains present.

- [ ] **Step 3: Restart and test Attu**

```powershell
docker compose --project-name knowledge-service --env-file .env.postgres -f compose.yml restart attu
docker compose --project-name knowledge-service --env-file .env.postgres -f compose.yml up -d attu
docker compose --project-name knowledge-service --env-file .env.postgres -f compose.yml ps

$attuStatus = try {
  (Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:3000' -TimeoutSec 10).StatusCode
} catch {
  [int]$_.Exception.Response.StatusCode
}
$attuStatus
```

Expected: Attu returns an HTTP response after its restart, and the four-service chain remains running.

- [ ] **Step 4: Review the final state**

```powershell
docker compose --project-name knowledge-service --env-file .env.postgres -f compose.yml ps
git diff --check
git status --short
git diff -- compose.yml docs/superpowers/specs/2026-09-30-unified-data-services-compose-design.md docs/superpowers/plans/2026-09-30-unified-data-services-compose.md
```

Expected: only the intended infrastructure and documentation files changed; `.env.postgres`, the snapshot, inspect metadata, and logs remain outside Git.

### Rollback Procedure: Embedded etcd Only

This procedure is an emergency rollback to the captured legacy embedded-etcd deployment. It is not the target architecture. Never start embedded-etcd Milvus while external etcd is running.

1. Capture logs and preserve the snapshot before removing containers:

```powershell
docker compose --project-name knowledge-service --env-file .env.postgres -f compose.yml logs --no-color etcd milvus attu |
  Set-Content -LiteralPath (Join-Path $cutoverDir 'failed-cutover.log')
Copy-Item -LiteralPath (Join-Path $cutoverDir 'pre-milvus-cutover.db') -Destination (Join-Path $cutoverDir 'pre-milvus-cutover.rollback-copy.db')
```

2. Stop and remove Attu and Milvus, then stop and remove external etcd before starting the rollback container:

```powershell
docker compose --project-name knowledge-service --env-file .env.postgres -f compose.yml stop attu milvus
docker compose --project-name knowledge-service --env-file .env.postgres -f compose.yml rm -f attu milvus
docker compose --project-name knowledge-service --env-file .env.postgres -f compose.yml stop etcd
docker compose --project-name knowledge-service --env-file .env.postgres -f compose.yml rm -f etcd

$externalEtcd = docker ps -a --filter 'name=^/knowledge-etcd$' --quiet
if ($externalEtcd) { throw 'External etcd still exists; embedded rollback must not start' }
```

3. Recreate the captured embedded-etcd Milvus definition. Prefer the saved inspect metadata; the following command is the known rollback-only definition. Do not invoke the vendor launcher because it may rewrite configuration files.

```powershell
docker run -d `
  --name milvus-standalone `
  --restart unless-stopped `
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
  -p 127.0.0.1:2379:2379 `
  --health-cmd='curl -f http://localhost:9091/healthz' `
  --health-interval=30s `
  --health-start-period=90s `
  --health-timeout=20s `
  --health-retries=3 `
  milvusdb/milvus:v3.0.2 `
  milvus run standalone

docker run -d `
  --name attu `
  --restart unless-stopped `
  -e MILVUS_ADDRESS=host.docker.internal:19530 `
  -p 3000:3000 `
  zilliz/attu:v3.0.0-beta.6
```

4. Wait for Milvus health and verify `knowledge_records` again. Never delete the Milvus data directory, the saved snapshot, or the PostgreSQL named volume. If snapshot restoration is required, stop all etcd/Milvus writers and perform it as a separately reviewed recovery operation rather than improvising during rollback.
