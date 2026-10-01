#!/usr/bin/env bash
# docker-compose.yml 基础设施验证脚本（可重复执行，不会丢数据）
#
# 覆盖：
#   1. 渲染配置：端口只绑本机、milvus 无内嵌 etcd、数据目录在 ./volumes 下
#   2. 功能连通：milvus / etcd / minio / postgres / attu
#   3. 冷启动竞争：down + up ×2，检查 etcd 真实连接故障必须为 0
#   4. 恢复能力：etcd 单独重启（依赖 restart:true 传播）、milvus 单独重启
#   5. 持久化：postgres 探针表与 milvus 集合在重启前后必须一致
#
# 注意：脚本只执行 `docker compose down`，绝不使用 `down -v`。
#
# 用法：bash scripts/verify-compose-stack.sh
set -u
cd "$(dirname "$0")/.."
ROOT="$(pwd -W 2>/dev/null || pwd)"

PASS=0; FAIL=0
log() { echo "[$(date +%H:%M:%S)] $*"; }
ok()  { echo "  ✔ PASS  $*"; PASS=$((PASS+1)); }
bad() { echo "  ✘ FAIL  $*"; FAIL=$((FAIL+1)); }
skip() { echo "  – SKIP  $*"; }

healthz()     { curl -s -o /dev/null -m 5 -w '%{http_code}' http://127.0.0.1:9091/healthz 2>/dev/null; }
collections() { curl -s -m 10 -X POST http://127.0.0.1:19530/v2/vectordb/collections/list \
                  -H 'Content-Type: application/json' -d '{"dbName":"default"}' 2>/dev/null; }
state()       { docker inspect -f '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}' "$1" 2>/dev/null; }

# compose 项目名（用于拼容器网络）
COMPOSE_PROJECT="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' knowledge-postgres 2>/dev/null)"
[ -n "$COMPOSE_PROJECT" ] || COMPOSE_PROJECT="knowledge-service"

wait_state() {  # $1=容器 $2=期望状态 $3=超时秒
  local t=0
  while [ "$t" -lt "$3" ]; do [ "$(state "$1")" = "$2" ] && return 0; sleep 5; t=$((t+5)); done
  return 1
}

# etcd 真实连接故障条数；排除 Milvus 探测鉴权的良性告警
etcd_faults() {
  docker logs milvus-standalone 2>&1 \
    | grep -i 'etcd' \
    | grep -v 'authentication is not enabled' \
    | grep -icE 'deadline|refused|unavailable|no such host|rpc error'
}

# postgres 探针表行数（用本地 socket 免密）
pg_probe() {
  docker exec knowledge-postgres psql -U postgres -d knowledge -tAc \
    "select count(*) from _compose_probe;" 2>/dev/null | tr -d '\r'
}

echo "==================== 1. 渲染配置 ===================="
docker compose config --quiet && ok "docker compose config 校验通过" || bad "config 校验失败"

render="$(docker compose config 2>/dev/null)"
echo "$render" | grep -qE 'ETCD_USE_EMBED|ETCD_DATA_DIR' \
  && bad "渲染结果仍含内嵌 etcd 配置（ETCD_USE_EMBED / ETCD_DATA_DIR）" \
  || ok "milvus 无内嵌 etcd 配置"

# 所有 published 端口必须是 127.0.0.1
bad_hosts="$(echo "$render" | grep -A2 'published:' | grep 'host_ip:' | grep -v '127.0.0.1' | wc -l)"
[ "$bad_hosts" -eq 0 ] && ok "全部 published 端口只绑 127.0.0.1" || bad "有 $bad_hosts 个端口未绑 127.0.0.1"

# 四个数据目录必须都在 ./volumes 下
missing=0
for d in postgres etcd minio milvus; do
  echo "$render" | grep -qi "volumes.${d}\\\\*$" || echo "$render" | grep -qi "volumes.${d}" || missing=$((missing+1))
done
[ "$missing" -eq 0 ] && ok "postgres/etcd/minio/milvus 数据目录都在 ./volumes 下" || bad "有 $missing 个数据目录不在 ./volumes 下"

echo "$render" | grep -q 'knowledge_postgres_data' \
  && bad "仍在使用 Docker 命名卷 knowledge_postgres_data" \
  || ok "不使用 Docker 命名卷（数据全部落盘到宿主机目录）"

echo
echo "==================== 2. 功能连通 ===================="
[ "$(healthz)" = "200" ] && ok "milvus healthz=200" || bad "milvus healthz=$(healthz)"

docker exec milvus-etcd etcdctl --endpoints=http://127.0.0.1:2379 endpoint health >/dev/null 2>&1 \
  && ok "etcd endpoint health 正常" || bad "etcd endpoint health 异常"

docker exec milvus-etcd etcdctl --endpoints=http://127.0.0.1:2379 get --prefix --keys-only 'by-dev/meta/session/' 2>/dev/null | grep -q by-dev \
  && ok "etcd 中存在 Milvus 会话元数据（by-dev/meta/session/*）" || bad "etcd 中缺少 Milvus 会话元数据"

docker exec milvus-minio sh -c 'mc alias set l http://127.0.0.1:9000 "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" >/dev/null 2>&1; mc ls l' 2>/dev/null | grep -q 'a-bucket' \
  && ok "minio 中存在 Milvus 桶 a-bucket" || bad "minio 中缺少 a-bucket"

docker exec knowledge-postgres pg_isready -U postgres >/dev/null 2>&1 \
  && ok "postgres 接受连接" || bad "postgres 未就绪"

pgv="$(docker exec knowledge-postgres cat /var/lib/postgresql/18/docker/PG_VERSION 2>/dev/null | tr -d '\r')"
[ "$pgv" = "18" ] && ok "postgres 集群版本 PG_VERSION=18（bind 目录内）" || bad "PG_VERSION 异常：'$pgv'"

docker exec knowledge-postgres psql -U postgres -tAc "select 1 from pg_database where datname='knowledge';" 2>/dev/null | grep -q 1 \
  && ok "业务库 knowledge 存在" || bad "业务库 knowledge 不存在"

app_role_ok="$(docker exec knowledge-postgres psql -U postgres -tAc \
  "select (rolcanlogin and not rolsuper and not rolcreatedb and not rolcreaterole) from pg_roles where rolname='knowledge_app';" 2>/dev/null | tr -d '\r')"
[ "$app_role_ok" = "t" ] \
  && ok "应用角色 knowledge_app 存在，且可登录、无 super/createdb/createrole" \
  || bad "应用角色 knowledge_app 缺失或权限过大（POSTGRES_APP_USER 需要 initdb 脚本才生效）"

app_ddl="$(docker exec knowledge-postgres psql -U postgres -tAc \
  "select has_schema_privilege('knowledge_app','public','CREATE');" 2>/dev/null | tr -d '\r')"
[ "$app_ddl" = "f" ] && ok "应用角色无 public schema DDL 权限（最小授权）" || bad "应用角色拥有 DDL 权限"

app_login="$(docker run --rm --network "${COMPOSE_PROJECT}_default" \
  -e PGPASSWORD="$(docker exec knowledge-postgres printenv POSTGRES_APP_PASSWORD)" \
  postgres:18.6-bookworm psql -h knowledge-postgres -p 5432 -U knowledge_app -d knowledge \
  -tAc "select current_user;" 2>/dev/null | tr -d '\r')"
[ "$app_login" = "knowledge_app" ] \
  && ok "应用角色经 compose 网络以 scram 登录成功（非 loopback 的 trust 通道）" \
  || bad "应用角色跨容器登录失败（返回 '$app_login'）"

app_scram="$(docker exec knowledge-postgres psql -U postgres -tAc \
  "select (rolpassword like 'SCRAM-SHA-256\$%') from pg_authid where rolname='knowledge_app';" 2>/dev/null | tr -d '\r')"
[ "$app_scram" = "t" ] && ok "应用角色口令以 SCRAM-SHA-256 存储" || bad "应用角色口令不是 SCRAM-SHA-256 存储"

app_bad="$(docker run --rm --network "${COMPOSE_PROJECT}_default" -e PGPASSWORD=definitely-wrong-password \
  postgres:18.6-bookworm psql -h knowledge-postgres -p 5432 -U knowledge_app -d knowledge \
  -tAc "select 1;" 2>&1 | grep -c 'password authentication failed')"
[ "$app_bad" -ge 1 ] && ok "错误口令被拒绝（反证上一步走的是 scram 而非 trust）" || bad "错误口令未被拒绝"

attu_code="$(curl -s -o /dev/null -m 5 -w '%{http_code}' http://127.0.0.1:30001/ 2>/dev/null)"
case "$attu_code" in
  2*|3*|401) ok "attu 在 127.0.0.1:30001 可访问（http=$attu_code）" ;;
  *)         bad "attu 在 127.0.0.1:30001 不可访问（http=$attu_code）" ;;
esac
netstat -ano 2>/dev/null | grep LISTENING | grep -qE ':3000\s' \
  && bad "旧端口 3000 仍在监听" || ok "旧端口 3000 已不再监听"

# 未声明端口的服务不应映射到宿主
netstat -ano 2>/dev/null | grep LISTENING | grep -qE ':2380\s' \
  && bad "etcd peer 端口 2380 暴露到宿主" || ok "etcd peer 端口 2380 未暴露到宿主"

echo
echo "==================== 3. 基线快照 ===================="
docker exec knowledge-postgres psql -U postgres -d knowledge -tAc \
  "create table if not exists _compose_probe(id serial primary key, created_at timestamptz default now());" >/dev/null 2>&1
docker exec knowledge-postgres psql -U postgres -d knowledge -tAc \
  "insert into _compose_probe default values;" >/dev/null 2>&1
BASE_COLLECTIONS="$(collections)"
BASE_PROBE="$(pg_probe)"
log "集合=$BASE_COLLECTIONS  探针行数=$BASE_PROBE"
log "内嵌 etcd：$(docker logs milvus-standalone 2>&1 | grep -o 'UseEmbed[^,]*' | sort -u | head -1)"
log "milvus 内 etcd 数据目录：$(docker exec milvus-standalone sh -c '[ -d /var/lib/milvus/etcd ] && echo 存在 || echo 不存在')"
log "milvus bind 源：$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/var/lib/milvus"}}{{.Source}}{{end}}{{end}}' milvus-standalone)"

echo
echo "==================== 4. 冷启动竞争测试（down + up）×2 ===================="
for round in 1 2; do
  log "--- 第 $round 轮 ---"
  docker compose down >/dev/null 2>&1
  start=$(date +%s)
  if docker compose up -d --wait --wait-timeout 420 >/tmp/ks-up-$round.log 2>&1; then
    ok "第 $round 轮冷启动成功（$(( $(date +%s) - start ))s，含 postgres/milvus/attu 全部 healthy）"
  else
    bad "第 $round 轮冷启动失败"; tail -20 /tmp/ks-up-$round.log
  fi
  f=$(etcd_faults)
  [ "$f" -eq 0 ] && ok "第 $round 轮 milvus↔etcd 无真实连接故障" || bad "第 $round 轮有 $f 条 etcd 连接故障"
  [ "$(collections)" = "$BASE_COLLECTIONS" ] \
    && ok "第 $round 轮 milvus 集合未丢：$BASE_COLLECTIONS" \
    || bad "第 $round 轮 milvus 集合变化：基线=$BASE_COLLECTIONS 现值=$(collections)"
  now_probe="$(pg_probe)"
  [ -n "$now_probe" ] && [ "$now_probe" -ge "$BASE_PROBE" ] \
    && ok "第 $round 轮 postgres 数据未丢（探针行数 $BASE_PROBE -> $now_probe）" \
    || bad "第 $round 轮 postgres 数据异常（探针行数 基线=$BASE_PROBE 现值='$now_probe'）"
done

echo
echo "==================== 5a. docker compose restart etcd：restart:true 是否连带重启 milvus ===================="
milvus_before="$(docker inspect -f '{{.State.StartedAt}}' milvus-standalone)"
docker compose restart etcd >/dev/null 2>&1
wait_state milvus-etcd running/healthy 120 && ok "etcd 重启后 healthy" || bad "etcd 重启后未 healthy"
wait_state milvus-standalone running/healthy 360 && ok "milvus 最终 healthy" || bad "milvus 未在 360s 内恢复 healthy"
milvus_after="$(docker inspect -f '{{.State.StartedAt}}' milvus-standalone)"
[ "$milvus_before" != "$milvus_after" ] \
  && ok "compose 重启 etcd 时 milvus 被连带重启（StartedAt 已变化）" \
  || bad "milvus 未被连带重启（StartedAt 未变化），restart:true 未生效"
[ "$(collections)" = "$BASE_COLLECTIONS" ] && ok "集合未丢：$BASE_COLLECTIONS" || bad "集合变化：$(collections)"

echo
echo "==================== 5b. 原生 docker restart etcd：milvus 能否自愈 ===================="
log "注意：docker restart 是原生命令，Compose 感知不到，restart:true 不会传播；本阶段验证自愈能力"
docker restart milvus-etcd >/dev/null
wait_state milvus-etcd running/healthy 120 && ok "etcd 重启后 healthy" || bad "etcd 重启后未 healthy"
wait_state milvus-standalone running/healthy 360 \
  && ok "milvus 在 etcd 短暂下线后自行恢复 healthy（未重启容器）" \
  || bad "milvus 未在 360s 内恢复 healthy"
[ "$(collections)" = "$BASE_COLLECTIONS" ] && ok "集合未丢：$BASE_COLLECTIONS" || bad "集合变化：$(collections)"
log "  提示：此阶段故意让 etcd 下线，milvus 期间的 connection refused 属预期重连噪声；冷启动阶段必须为 0"

echo
echo "==================== 6. milvus / attu 单独重启 ===================="
docker restart milvus-standalone >/dev/null
wait_state milvus-standalone running/healthy 360 && ok "milvus 重启后 healthy" || bad "milvus 重启后未 healthy"
[ "$(healthz)" = "200" ] && ok "milvus healthz=200" || bad "milvus healthz=$(healthz)"
[ "$(collections)" = "$BASE_COLLECTIONS" ] && ok "milvus 重启后集合未丢" || bad "milvus 重启后集合变化"

echo
echo "==================== 结果：PASS=$PASS  FAIL=$FAIL ===================="
[ "$FAIL" -eq 0 ]
