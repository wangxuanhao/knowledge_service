-- ════════════════════════════════════════════════════════════════════════════
--  0003：supersedes 链的**延迟**校验
--
--  问题
--    0001 给 supersedes 链条加的是**即时**组合外键：
--      ontology_operations.(project_id, draft_id, supersedes_operation_id)
--        → ontology_operations.(project_id, draft_id, id)
--    但"替代"语义天然允许**同一事务内乱序插入**：先写"新的替代旧的"，
--    被替代那行可能在同一事务稍后才落库。即时校验会把这种中间态直接拒掉。
--
--    替代语义本身就要求延迟校验：被替代行不必在本行之前落库。0001 的即时
--    外键会把这种合法写入拒掉，不是"更严格"，而是"语义不对"。
--
--  为什么不能只去掉外键、改用触发器或应用层 SQL 校验
--    外键与触发器负责的是**不同类别**的不变量，谁也替代不了谁：
--      * 引用完整性（被替代的行确实存在，且同项目同草案）→ 外键。
--        触发器/应用层校验存在**竞态窗口**：两个并发事务各自看不到对方未提交
--        的行，都可能放行，提交后出现悬空引用。外键由引擎在校验点原子完成。
--      * 禁止成环（递归可达自身）→ 触发器。外键无法表达。
--    因此这里是"两者都要"，各自负责其能正确保证的那部分。
--
--  改动一：两个自引用外键改为 DEFERRABLE INITIALLY DEFERRED
--    延迟只在**自引用**这两个上（supersedes_operation_id / supersedes_decision_id）；
--    其余 29 个外键都是"被引用行必须先存在"的正常依赖，保持即时，
--    这样错误能在最早的语句上暴露，而不是拖到提交。
--
--  改动二：新增**延迟约束触发器**做全图成环检测
--    0001 的 BEFORE INSERT 成环守卫只看得到**当时已存在的行**，因此查不出
--    "分三步插入、末尾才闭合"的 3 元环（插第三步时被替代行尚不存在，递归就此断开）。
--    实测确认：三元环 a→c、b→a、c→b 在 0001 下能被完整提交。
--    延迟约束触发器在 COMMIT 时触发，此时全部行都已落库，递归能走通，
--    因此任何长度的环都无法提交。
--
--    保留 0001 的即时守卫：常见情形（自环、两元环）能在**出错语句**上立刻报错，
--    定位成本远低于"提交时才失败"。两层是互补，不是重复。
-- ════════════════════════════════════════════════════════════════════════════

-- ── 改动一：自引用外键改为延迟 ──────────────────────────────────────────────
--
-- PostgreSQL 不支持直接修改外键的可延迟性，只能重建。
-- 约束名用显式命名（0001 里那套是自动生成的、还被截断到 63 字符），
-- 便于后续迁移稳定引用。

ALTER TABLE ontology_operations
    DROP CONSTRAINT ontology_operations_project_id_draft_id_supersedes_operati_fkey,
    ADD CONSTRAINT ontology_operations_supersedes_fk
        FOREIGN KEY (project_id, draft_id, supersedes_operation_id)
        REFERENCES ontology_operations (project_id, draft_id, id)
        DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE ontology_review_decisions
    DROP CONSTRAINT ontology_review_decisions_project_id_draft_id_supersedes_d_fkey,
    ADD CONSTRAINT ontology_review_decisions_supersedes_fk
        FOREIGN KEY (project_id, draft_id, supersedes_decision_id)
        REFERENCES ontology_review_decisions (project_id, draft_id, id)
        DEFERRABLE INITIALLY DEFERRED;


-- ── 改动二：COMMIT 时的全图成环检测 ────────────────────────────────────────

-- 操作：延迟成环检测
--   只有落在环上或指向环的行才会命中（其余行的递归结果不含自身）。
--   递归用 UNION 去重，保证有限终止。
CREATE OR REPLACE FUNCTION check_ontology_operation_cycle()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.supersedes_operation_id IS NOT NULL AND EXISTS (
        WITH RECURSIVE supersession(id) AS (
            VALUES (NEW.supersedes_operation_id)
            UNION
            SELECT operation.supersedes_operation_id
              FROM ontology_operations AS operation
              JOIN supersession ON operation.id = supersession.id
             WHERE operation.project_id = NEW.project_id
               AND operation.draft_id = NEW.draft_id
               AND operation.supersedes_operation_id IS NOT NULL
        )
        SELECT 1 FROM supersession WHERE id = NEW.id
    ) THEN
        RAISE EXCEPTION 'ontology operation supersession cycle'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NULL;   -- AFTER 触发器的返回值无意义
END;
$$;

CREATE CONSTRAINT TRIGGER ontology_operations_deferred_cycle_guard
    AFTER INSERT ON ontology_operations
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION check_ontology_operation_cycle();

-- 决定：延迟成环检测（同上，作用于 supersedes_decision_id）
CREATE OR REPLACE FUNCTION check_ontology_decision_cycle()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.supersedes_decision_id IS NOT NULL AND EXISTS (
        WITH RECURSIVE supersession(id) AS (
            VALUES (NEW.supersedes_decision_id)
            UNION
            SELECT decision.supersedes_decision_id
              FROM ontology_review_decisions AS decision
              JOIN supersession ON decision.id = supersession.id
             WHERE decision.project_id = NEW.project_id
               AND decision.draft_id = NEW.draft_id
               AND decision.supersedes_decision_id IS NOT NULL
        )
        SELECT 1 FROM supersession WHERE id = NEW.id
    ) THEN
        RAISE EXCEPTION 'ontology decision supersession cycle'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER ontology_decisions_deferred_cycle_guard
    AFTER INSERT ON ontology_review_decisions
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION check_ontology_decision_cycle();
