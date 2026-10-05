-- ============================================================================
-- 0008_users.sql —— 用户与角色（认证 / 授权基线）
-- ============================================================================
-- 目的：给服务加上登录身份。权限模型保持最简单的两档角色：
--   * admin  管理员：可写入（项目管理、知识写入/修订/删除、本体建模与发布等）；
--   * viewer 只读用户：只能问答、检索、查看脑图/台账/本体，不能做任何写入。
--
-- 设计取舍：
--   1) 只用「角色」一列表达权限，不引入 roles/permissions/user_roles 多表 ——
--      当前只有「能写 / 只能读」两类真实需求，多加表只会让判断链路变长、更易错。
--      将来若出现「可审核但不可建模」这类细分，再用独立迁移扩成多表，不破坏本列。
--   2) username 唯一：登录名。应用层在写入前统一转小写，保证 "Alice" 与 "alice"
--      不会注册成两个人。这里用普通 UNIQUE（数据库按原样区分大小写），
--      规范化责任放在应用层一个出口，避免数据库 locale/排序规则差异。
--   3) password_hash 存的是「算法$迭代$盐$摘要」的自描述字符串，不是裸口令；
--      更换哈希算法或迭代次数时，旧记录仍能从字符串头部识别怎么校验。
--   4) is_active：禁用账号比删除更安全——禁用后该用户历史操作的 actor 仍可追溯，
--      且其 token 立即失效（中间件同时校验 is_active）。
-- ============================================================================

CREATE TABLE users (
    id            text PRIMARY KEY,
    username      text NOT NULL UNIQUE,
    password_hash text NOT NULL,
    display_name  text NOT NULL DEFAULT '',
    role          text NOT NULL CHECK (role IN ('admin', 'viewer')),
    is_active     boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL,
    updated_at    timestamptz NOT NULL
);

-- 登录主路径：按用户名取整行（用户名已在应用层小写化）。
CREATE INDEX users_username_active ON users (username, is_active);

-- ── 应用角色授权 ────────────────────────────────────────────────────────────
-- 0001 已配置 ALTER DEFAULT PRIVILEGES，owner 新建的表默认就把 DML 授给
-- knowledge_app；这里再显式 GRANT 一次，避免「默认特权在该环境未生效」时
-- 应用登录即报 permission denied（这类问题只在生产暴露）。
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'knowledge_app') THEN
        GRANT SELECT, INSERT, UPDATE ON users TO knowledge_app;
    ELSE
        RAISE NOTICE '应用角色 knowledge_app 不存在，跳过 users 授权（请先执行 initdb 脚本）';
    END IF;
END;
$$;
