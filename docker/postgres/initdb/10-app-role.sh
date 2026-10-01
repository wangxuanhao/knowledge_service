#!/bin/bash
# 创建/同步 PostgreSQL 应用角色（POSTGRES_APP_USER / POSTGRES_APP_PASSWORD）
#
# 为什么需要这个脚本：
#   postgres 官方镜像只认识 POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB，
#   不认 POSTGRES_APP_USER / POSTGRES_APP_PASSWORD —— 不挂载初始化脚本时，
#   这两个变量完全是空转，库里只有超级用户一个角色（已实测）。
#
# 什么时候执行：
#   1) 新装：镜像在「空数据目录」首次初始化时自动执行 /docker-entrypoint-initdb.d/*.sh
#   2) 已有集群：initdb 不会再跑，需要手工执行一次同一脚本：
#        docker exec knowledge-postgres bash /docker-entrypoint-initdb.d/10-app-role.sh
#      脚本是幂等的，重复执行只会同步密码，不会破坏已有数据。
#
# 权限口径（依据 docs/2026-09-30-PostgreSQL替换与本体知识服务优化规划.md §154）：
#   应用角色 = 可登录、无 SUPERUSER / CREATEDB / CREATEROLE / REPLICATION / BYPASSRLS、
#   无 schema DDL 权限（不给 public 上的 CREATE），只拿连接 + 读写 DML。
#   迁移用的 DDL 角色单独建，不在本脚本职责内。

set -euo pipefail

if [ -z "${POSTGRES_APP_USER:-}" ] || [ -z "${POSTGRES_APP_PASSWORD:-}" ]; then
  echo "[app-role] POSTGRES_APP_USER / POSTGRES_APP_PASSWORD 未设置，跳过应用角色创建" >&2
  exit 0
fi

echo "[app-role] 创建/同步应用角色 ${POSTGRES_APP_USER}（无 DDL、无超级权限）"

psql -v ON_ERROR_STOP=1 \
  --username "${POSTGRES_USER:-postgres}" \
  --dbname "${POSTGRES_DB:-postgres}" \
  -v app_user="${POSTGRES_APP_USER}" \
  -v app_pw="${POSTGRES_APP_PASSWORD}" \
  -v app_db="${POSTGRES_DB:-postgres}" <<'EOSQL'
-- 不存在才建（\gexec 在结果为空时不执行任何语句）
SELECT format('CREATE ROLE %I LOGIN', :'app_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user')
\gexec

-- 每次都同步口令与权限位，保证与 .env 一致；口令按服务端 password_encryption 加密存储
SELECT format(
  'ALTER ROLE %I WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
  :'app_user', :'app_pw')
\gexec

GRANT CONNECT ON DATABASE :"app_db" TO :"app_user";
GRANT USAGE ON SCHEMA public TO :"app_user";

-- 已存在对象的读写权限（不授予 DDL）
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"app_user";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"app_user";

-- 复核：输出角色属性与 public schema 上的实际权限
SELECT rolname, rolcanlogin AS can_login, rolsuper AS is_super,
       rolcreatedb AS can_createdb, rolcreaterole AS can_createrole
FROM pg_roles WHERE rolname = :'app_user';

SELECT has_schema_privilege(:'app_user', 'public', 'CREATE') AS has_ddl_create,
       has_schema_privilege(:'app_user', 'public', 'USAGE')  AS has_schema_usage,
       has_database_privilege(:'app_user', :'app_db', 'CONNECT') AS can_connect;
EOSQL

echo "[app-role] 完成"
