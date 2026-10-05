-- ============================================================================
-- 0009_roles_three_tiers.sql —— 角色从两档扩为三档（新增超级管理员）
-- ============================================================================
-- 背景：0008 只有 admin / viewer。真实运维需要一个「系统之根」：
--   * superadmin 超级管理员：能管理**所有**账号（含管理员），是权限体系的最终入口；
--   * admin      管理员：能写入、建模发布、管理普通账号，但不能碰超级管理员；
--   * viewer     只读用户：只能检索、问答、看脑图。
--
-- 本迁移只做一件事：把 role 的取值约束从两档换成三档。
--   * 不在这里改数据 —— 首个 superadmin 由应用启动时的装配逻辑保证
--     （空库播种；老库把种子管理员提升为超级管理员），因为口令哈希要走应用
--     的安全原语，放在 SQL 里反而要重复实现一遍。
--
-- 约束名用动态 SQL 处理：PostgreSQL 自动命名通常是 users_role_check，
-- 但不假定环境一定没被人改过名字 —— 查出该列上现存的 CHECK 约束再替换，
-- 找不到时（约束名异常）直接 ADD，避免迁移因「假设的名字不存在」而失败。
-- ============================================================================

DO $$
DECLARE
    constraint_name text;
BEGIN
    -- 找到 users.role 上当前那条 CHECK 约束（0008 建的两档约束）。
    SELECT con.conname INTO constraint_name
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = ANY (con.conkey)
    WHERE rel.relname = 'users'
      AND att.attname = 'role'
      AND con.contype = 'c'
    LIMIT 1;

    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE users DROP CONSTRAINT %I', constraint_name);
    END IF;

    -- 换成三档约束；旧数据里的 admin / viewer 都仍合法，不会因加约束失败。
    ALTER TABLE users
        ADD CONSTRAINT users_role_check
        CHECK (role IN ('superadmin', 'admin', 'viewer'));
END;
$$;

-- 角色说明不入库（避免多维护一张表），口径以应用层 security_middleware 为准。
