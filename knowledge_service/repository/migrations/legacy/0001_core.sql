-- ============================================================================
-- 0001_core.sql —— PostgreSQL 基线 schema（T02）
-- ============================================================================
-- 依据：docs/2026-09-30-PostgreSQL替换与本体知识服务优化规划.md §5.2
-- 约束：DDL 与语义**不凭印象手写**，逐项有据。
--
-- 执行角色：迁移角色（owner）。应用角色 knowledge_app 无 DDL 权限，见
--           docker/postgres/initdb/10-app-role.sh 与 repository/migrate.py。
--
-- ── 类型映射决策（每条都对应一个具体风险，不要随意更改）─────────────────
--
--   1) ID / 名称 / 文本  →  text
--      业务 ID 与语义 IRI 都不是 UUID，规划 §5.2 明确「不能假设历史 ID 都是 UUID」。
--
--   2) 时间戳  →  timestamptz
--      规划 §5.2「时间用 timestamptz，内部规范化为 UTC；API 序列化行为冻结」。
--      读取层由 connection.TimestamptzAsTextLoader 还原为 `utc_now()` 同格式
--      字符串（UTC + 微秒 + Z），保证业务层输出逐字节一致。
--      写入仍可直接传 ISO 字符串（PostgreSQL 走赋值转换）。
--
--   3) 不透明 JSON 载荷  →  **text**（不是 jsonb）
--      这是本项目最容易被做错的一处。jsonb **会规范化键序并去重**，字符串
--      无法与原样字节一一对应；而代码里有针对 JSON 内容计算的指纹/哈希
--      （如 neo4j projection_digest、fact_key、discovery 指纹），
--      一旦改用 jsonb，同样的输入会产生不同指纹 → 静默破坏幂等与去重。
--      因此：**会被哈希或原样往返的 JSON 一律保持 text**。
--
--      `CHECK (col IS JSON)` **只在下方 9 列上保留**（见各行
--      「必须是合法 JSON」注释）。其余列不加：没有真实数据背书就加严约束，
--      等于让 PostgreSQL 拒绝掉本来合法的写入 —— 这类回归只会在生产暴露。
--      若要收紧，应作为独立迁移，先用查询证明存量数据全部合规。
--
--   4) 需要 SQL 内过滤的 JSON  →  仍是 text，但建**表达式索引**
--      `CREATE INDEX ... USING gin ((col::jsonb) jsonb_path_ops)`。
--      这样既有字节精确的存储，又能索引下推（`(col::jsonb) @> %s::jsonb`）。
--      规划 §5.2「只对实测常用路径建表达式/GIN 索引」—— 故本文件只建
--      检索必需的那几条，性能索引在 T05 按实测补。
--
--   5) INTEGER → integer；REAL → double precision。
--      **不额外添加范围 CHECK**（例如 record_versions.version 只声明
--      `integer NOT NULL`，这里同样不加 CHECK），理由同第 3 条。
--
--   6) record_fts：真实表 + GIN 索引
--      真实表可并发写入、不依赖虚拟表；用 `pg_trgm` 的 GIN（字符 n-gram，
--      **中文无需分词词典**）承接中文**子串**匹配，可直接支撑 LIKE '%…%' 与
--      相似度查询；英文另建 `to_tsvector('english', ...)` 的 GIN 承接全文检索。
--      规划 §5.4 要求的「受控中文分词（zhparser/pg_jieba）」需要镜像支持相应
--      扩展，属 T06 的独立决策项，本基线不引入不可用的依赖。
--
--      写入契约：按 (project_id, record_id) **先删后插**，只保留当前版本
--      （core.py:1299）。故 (project_id, record_id) 可安全作为主键。
--
--   7) 受控删除开关用会话级 GUC `knowledge.ontology_history_delete_allowed`
--      表达，供不可变历史触发器读取。默认 0（禁止），只有受控维护路径在
--      事务内 `SET LOCAL` 为 '1' 才允许删除不可变历史（见 fn 定义与各
--      immutable 触发器）。
--
--   8) 幂等写入一律用 `ON CONFLICT`：重复键忽略用 `DO NOTHING`，覆盖用
--      `DO UPDATE`（T03 在 SQL 层落地，不改变语义）。
--
--   9) 触发器：同一表、同一时机(BEFORE INSERT)的多个条件合并为一个 plpgsql
--      函数，避免函数体重复同一段递归 CTE。但**拒绝集合必须完全一致**，
--      故每个函数内逐条判断（见各函数注释里的 [A]..[F] 标记）。
-- ============================================================================

-- 扩展需要在 owner/超级用户下创建（应用角色无此权限）
CREATE EXTENSION IF NOT EXISTS pg_trgm;


-- ════════════════════════════════════════════════════════════════════════════
--  基础域：项目 / 版本 / 本体 / 制品 / 配置
-- ════════════════════════════════════════════════════════════════════════════

CREATE TABLE projects (
    id          text PRIMARY KEY,
    name        text NOT NULL,
    metadata    text NOT NULL,
    created_at  timestamptz NOT NULL
);

-- 双时态版本表：一次修订修正一整条稳定记录。
--   * PK(project_id, id, version) —— 版本号在项目内唯一
--   * version_id 全局唯一 —— 供 Milvus/Neo4j 等投影按 version_id 精确关联
--   * superseded_at IS NULL 的部分唯一索引 —— 「当前版本」语义
CREATE TABLE record_versions (
    project_id     text NOT NULL REFERENCES projects(id),
    id             text NOT NULL,
    version        integer NOT NULL,
    version_id     text NOT NULL UNIQUE,
    payload        text NOT NULL,
    recorded_at    timestamptz NOT NULL,
    superseded_at  timestamptz,
    PRIMARY KEY (project_id, id, version)
);

-- 历史查询主索引：按项目 + 系统时间定位版本区间
CREATE INDEX versions_project ON record_versions (project_id, recorded_at, superseded_at);
-- 当前版本唯一性（部分唯一索引）
CREATE UNIQUE INDEX versions_current ON record_versions (project_id, id)
    WHERE superseded_at IS NULL;
-- 供 record_version_assertions 的复合外键引用
CREATE UNIQUE INDEX record_versions_provenance_fk
    ON record_versions (project_id, id, version_id);

-- 已发布本体版本（不可变快照）
CREATE TABLE ontologies (
    id          text PRIMARY KEY,
    project_id  text NOT NULL REFERENCES projects(id),
    turtle      text NOT NULL,
    summary     text NOT NULL,
    created_at  timestamptz NOT NULL,
    metadata    text NOT NULL DEFAULT '{}'
);
-- 复合外键目标：同一项目内引用本体版本
CREATE UNIQUE INDEX ontologies_project_id_fk ON ontologies (project_id, id);

-- 通用制品（任务收据、导入包、快照等），payload 为不透明 JSON
CREATE TABLE artifacts (
    id          text PRIMARY KEY,
    kind        text NOT NULL,
    project_id  text,
    payload     text NOT NULL
);

-- 进程级配置（如 storage_namespace，用于隔离投影命名空间）
CREATE TABLE service_settings (
    key    text PRIMARY KEY,
    value  text NOT NULL
);


-- ════════════════════════════════════════════════════════════════════════════
--  治理域：断言 / 事实键 / 摄取 / 消歧 / 合并
-- ════════════════════════════════════════════════════════════════════════════

CREATE TABLE assertions (
    id                   text PRIMARY KEY,
    project_id           text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind                 text NOT NULL,
    document_id          text,
    document_version_id  text,
    chunk_id             text,
    source_hash          text,
    start_char           integer,
    end_char             integer,
    quote                text,
    payload              text NOT NULL,
    status               text NOT NULL,
    canonical_record_id  text,
    decision_reason      text,
    decision_version     integer NOT NULL,
    created_at           timestamptz NOT NULL,
    decided_at           timestamptz,
    actor                text NOT NULL
);
CREATE INDEX assertions_canonical ON assertions (project_id, canonical_record_id, status);
CREATE INDEX assertions_document ON assertions (project_id, document_id, document_version_id);
CREATE INDEX assertions_project_status ON assertions (project_id, status, created_at);

-- 断言状态流转事件（追加写，不更新）
CREATE TABLE assertion_events (
    id                   text PRIMARY KEY,
    assertion_id         text NOT NULL REFERENCES assertions(id) ON DELETE CASCADE,
    project_id           text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    from_status          text,
    to_status            text NOT NULL,
    decision_version     integer NOT NULL,
    reason               text NOT NULL,
    actor                text NOT NULL,
    canonical_record_id  text,
    created_at           timestamptz NOT NULL
);
CREATE INDEX assertion_events_assertion
    ON assertion_events (project_id, assertion_id, decision_version);
-- 复合外键目标：record_version_assertions 需绑定到「事件 + 其规范记录」
CREATE UNIQUE INDEX assertion_events_provenance_fk
    ON assertion_events (project_id, assertion_id, id, canonical_record_id);

-- 事实键：同一项目内「同一事实」的唯一化，退役后释放唯一性
CREATE TABLE fact_keys (
    project_id           text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    fact_key             text NOT NULL,
    canonical_record_id  text NOT NULL,
    created_at           timestamptz NOT NULL,
    retired_at           timestamptz,
    PRIMARY KEY (project_id, fact_key, created_at)
);
CREATE UNIQUE INDEX fact_keys_current_key
    ON fact_keys (project_id, fact_key) WHERE retired_at IS NULL;
CREATE INDEX fact_keys_current_record
    ON fact_keys (project_id, canonical_record_id) WHERE retired_at IS NULL;

-- 摄取运行：每次尝试一行，retry_of 形成重试链
CREATE TABLE ingest_runs (
    id                   text PRIMARY KEY,
    project_id           text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    document_id          text NOT NULL,
    document_version_id  text NOT NULL,
    attempt              integer NOT NULL,
    retry_of             text REFERENCES ingest_runs(id),
    status               text NOT NULL,
    active_stage         text,
    readiness            text NOT NULL,
    counts               text NOT NULL,
    failure              text,
    version              integer NOT NULL,
    created_at           timestamptz NOT NULL,
    updated_at           timestamptz NOT NULL,
    UNIQUE (document_version_id, attempt)
);
CREATE INDEX ingest_runs_project_document ON ingest_runs (project_id, document_id, attempt);

-- 阶段输出：可断点重跑的最小单元
CREATE TABLE ingest_stage_outputs (
    project_id           text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    document_version_id  text NOT NULL,
    chunk_id             text NOT NULL,
    stage                text NOT NULL,
    input_hash           text NOT NULL,
    payload              text NOT NULL,
    created_at           timestamptz NOT NULL,
    PRIMARY KEY (document_version_id, chunk_id, stage, input_hash)
);

-- 实体合并账本（可逆）
CREATE TABLE merge_operations (
    id                text PRIMARY KEY,
    project_id        text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    operation         text NOT NULL,
    status            text NOT NULL,
    redirects         text NOT NULL,
    assertion_moves   text NOT NULL,
    before_state      text NOT NULL,
    after_state       text NOT NULL,
    expected_versions text NOT NULL,
    reversal_of       text REFERENCES merge_operations(id),
    created_at        timestamptz NOT NULL
);
CREATE INDEX merge_operations_project ON merge_operations (project_id, created_at);

-- 消歧审核（实体合并候选）
CREATE TABLE resolution_reviews (
    id                  text PRIMARY KEY,
    project_id          text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source_entity_id    text NOT NULL,
    candidate_entity_id text NOT NULL,
    score               double precision NOT NULL,
    status              text NOT NULL,
    decision_version    integer NOT NULL,
    payload             text NOT NULL,
    reason              text,
    actor               text,
    created_at          timestamptz NOT NULL,
    decided_at          timestamptz
);
CREATE INDEX resolution_reviews_project_status
    ON resolution_reviews (project_id, status, created_at);

-- 单次逻辑写入上下文：token 身份授权多阶段写入共享同一系统时间点
CREATE TABLE record_operation_reservations (
    token        text PRIMARY KEY,
    project_id   text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    recorded_at  timestamptz NOT NULL,
    UNIQUE (project_id, recorded_at)
);
CREATE INDEX record_operation_reservations_project
    ON record_operation_reservations (project_id, recorded_at);


-- ════════════════════════════════════════════════════════════════════════════
--  溯源域：活动 / 边 / 版本—断言绑定
-- ════════════════════════════════════════════════════════════════════════════

CREATE TABLE provenance_activities (
    id            text NOT NULL,
    project_id    text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind          text NOT NULL CHECK (kind IN (
                      'retrieval', 'answer', 'ontology_draft', 'ontology_publish')),
    status        text NOT NULL CHECK (status IN (
                      'running', 'completed', 'failed', 'cancelled')),
    -- payload 必须是合法 JSON
    payload       text NOT NULL CHECK (payload IS JSON),
    started_at    timestamptz NOT NULL,
    completed_at  timestamptz,
    UNIQUE (project_id, id)
);

CREATE TABLE provenance_edges (
    id          text PRIMARY KEY,
    project_id  text NOT NULL,
    activity_id text NOT NULL,
    source_ref  text NOT NULL,
    relation    text NOT NULL CHECK (relation IN (
                    'considered', 'used', 'offered', 'cites', 'supported-by', 'decided-by',
                    'extracted-from', 'sourced-from', 'processed-by', 'published-from',
                    'contains-operation', 'proposed-by', 'based-on')),
    target_ref  text NOT NULL,
    ordinal     integer NOT NULL CHECK (ordinal >= 0),
    -- payload 必须是合法 JSON
    payload     text NOT NULL CHECK (payload IS JSON),
    created_at  timestamptz NOT NULL,
    UNIQUE (project_id, activity_id, source_ref, relation, target_ref, ordinal),
    FOREIGN KEY (project_id, activity_id)
        REFERENCES provenance_activities (project_id, id) ON DELETE CASCADE
);
CREATE INDEX provenance_edges_activity ON provenance_edges (project_id, activity_id);
CREATE INDEX provenance_edges_source ON provenance_edges (project_id, source_ref);
CREATE INDEX provenance_edges_target ON provenance_edges (project_id, target_ref);

-- 版本—断言绑定：把「哪条断言支持了哪个版本」写成不可伪造的边
CREATE TABLE record_version_assertions (
    project_id         text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    record_id          text NOT NULL,
    record_version_id  text NOT NULL,
    assertion_id       text NOT NULL,
    assertion_event_id text NOT NULL,
    created_at         timestamptz NOT NULL,
    UNIQUE (project_id, record_version_id, assertion_id, assertion_event_id),
    FOREIGN KEY (project_id, record_id, record_version_id)
        REFERENCES record_versions (project_id, id, version_id) ON DELETE CASCADE,
    FOREIGN KEY (project_id, assertion_id, assertion_event_id, record_id)
        REFERENCES assertion_events (project_id, assertion_id, id, canonical_record_id)
        ON DELETE CASCADE
);
CREATE INDEX record_version_assertions_assertion
    ON record_version_assertions (project_id, assertion_id);
CREATE INDEX record_version_assertions_version
    ON record_version_assertions (project_id, record_version_id);


-- ════════════════════════════════════════════════════════════════════════════
--  本体治理域：草案 / 操作 / 决定 / 发布请求 / 历史修复
--  规划 §6.6：草案是基于 base ontology revision 的原子操作集合；
--            已发布本体不可原地编辑；发布必须绑定同一 revision 与指纹。
-- ════════════════════════════════════════════════════════════════════════════

CREATE TABLE ontology_drafts (
    id                     text PRIMARY KEY,
    project_id             text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    base_ontology_id       text,
    source_kind            text NOT NULL CHECK (source_kind IN (
                               'manual', 'turtle', 'import', 'ai', 'discovery', 'candidate')),
    status                 text NOT NULL CHECK (status IN (
                               'editing', 'submitted', 'reviewed', 'published',
                               'closed', 'stale_base', 'stale_source')),
    revision               integer NOT NULL CHECK (revision >= 1),
    title                  text NOT NULL,
    summary                text NOT NULL,
    -- source_context 必须是合法 JSON
    source_context         text NOT NULL CHECK (source_context IS JSON),
    -- validation_report 为 NULL 或必须是合法 JSON
    validation_report      text CHECK (validation_report IS NULL OR validation_report IS JSON),
    validation_fingerprint text,
    published_ontology_id  text,
    legacy_artifact_id     text,
    created_at             timestamptz NOT NULL,
    updated_at             timestamptz NOT NULL,
    UNIQUE (project_id, id),
    FOREIGN KEY (project_id, base_ontology_id)
        REFERENCES ontologies (project_id, id),
    FOREIGN KEY (project_id, published_ontology_id)
        REFERENCES ontologies (project_id, id)
);
CREATE INDEX ontology_drafts_project_status
    ON ontology_drafts (project_id, status, updated_at, id);

-- 草案操作（追加写、不可变；supersedes 形成修订链）
CREATE TABLE ontology_operations (
    id                       text PRIMARY KEY,
    project_id               text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    draft_id                 text NOT NULL,
    action                   text NOT NULL,
    target_iri               text NOT NULL,
    -- 以下 5 列均必须是合法 JSON
    before_json              text NOT NULL CHECK (before_json IS JSON),
    after_json               text NOT NULL CHECK (after_json IS JSON),
    evidence_json            text NOT NULL CHECK (evidence_json IS JSON),
    impact_json              text NOT NULL CHECK (impact_json IS JSON),
    validation_json          text NOT NULL CHECK (validation_json IS JSON),
    risk                     text NOT NULL,
    fingerprint              text NOT NULL,
    reason                   text,
    supersedes_operation_id  text,
    created_at               timestamptz NOT NULL,
    UNIQUE (project_id, id),
    UNIQUE (project_id, draft_id, id),
    FOREIGN KEY (project_id, draft_id)
        REFERENCES ontology_drafts (project_id, id) ON DELETE CASCADE,
    FOREIGN KEY (project_id, draft_id, supersedes_operation_id)
        REFERENCES ontology_operations (project_id, draft_id, id)
);
CREATE INDEX ontology_operations_draft
    ON ontology_operations (project_id, draft_id, created_at, id);
CREATE UNIQUE INDEX ontology_operations_identity_fingerprint
    ON ontology_operations (project_id, draft_id, id, fingerprint);
CREATE INDEX ontology_operations_supersedes
    ON ontology_operations (project_id, draft_id, supersedes_operation_id);

-- 审核决定（追加写、不可变）
CREATE TABLE ontology_review_decisions (
    id                    text PRIMARY KEY,
    project_id            text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    draft_id              text NOT NULL,
    operation_id          text NOT NULL,
    operation_fingerprint text NOT NULL,
    action                text NOT NULL CHECK (action IN ('approve', 'reject', 'request_changes')),
    reason                text,
    actor                 text NOT NULL,
    supersedes_decision_id text,
    created_at            timestamptz NOT NULL,
    UNIQUE (project_id, id),
    UNIQUE (project_id, draft_id, id),
    FOREIGN KEY (project_id, draft_id)
        REFERENCES ontology_drafts (project_id, id) ON DELETE CASCADE,
    FOREIGN KEY (project_id, draft_id, operation_id)
        REFERENCES ontology_operations (project_id, draft_id, id),
    FOREIGN KEY (project_id, draft_id, supersedes_decision_id)
        REFERENCES ontology_review_decisions (project_id, draft_id, id)
);
CREATE INDEX ontology_review_decisions_draft
    ON ontology_review_decisions (project_id, draft_id, created_at, id);
CREATE INDEX ontology_review_decisions_supersedes
    ON ontology_review_decisions (project_id, draft_id, supersedes_decision_id);

-- 发布请求：幂等键 + 请求哈希，保证重复提交不产生第二个本体版本
CREATE TABLE ontology_publish_requests (
    id                 text PRIMARY KEY,
    project_id         text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    draft_id           text NOT NULL,
    idempotency_key    text NOT NULL,
    request_hash       text NOT NULL,
    result_ontology_id text,
    created_at         timestamptz NOT NULL,
    completed_at       timestamptz,
    UNIQUE (project_id, draft_id, idempotency_key),
    FOREIGN KEY (project_id, draft_id)
        REFERENCES ontology_drafts (project_id, id) ON DELETE CASCADE,
    FOREIGN KEY (project_id, result_ontology_id)
        REFERENCES ontologies (project_id, id)
);

-- 历史修复台账（完全不可变，只允许插入新键）
CREATE TABLE ontology_history_repairs (
    repair_key          text PRIMARY KEY,
    schema_version      integer NOT NULL,
    reason              text NOT NULL,
    operations_repaired integer NOT NULL CHECK (operations_repaired >= 0),
    decisions_repaired  integer NOT NULL CHECK (decisions_repaired >= 0),
    repaired_at         timestamptz NOT NULL
);


-- ════════════════════════════════════════════════════════════════════════════
--  检索索引域：record_fts（中文子串匹配 + 英文全文检索）
--
--  设计（见文件头映射说明 6）：
--    * 真实表 + GIN 索引：可并发写入、不依赖虚拟表；
--    * 中文匹配用 pg_trgm（字符 n-gram，无需分词词典），
--      英文另建 tsvector GIN。规划 §5.4 要求的受控中文分词属 T06 决策项。
--
--  写入契约：按 (project_id, record_id) 先删后插，只保留当前版本。
-- ════════════════════════════════════════════════════════════════════════════

CREATE TABLE record_fts (
    project_id  text NOT NULL,
    record_id   text NOT NULL,
    kind        text NOT NULL,
    text        text NOT NULL,
    metadata    text NOT NULL DEFAULT '{}',
    PRIMARY KEY (project_id, record_id)
);
-- 中文/短词子串匹配（pg_trgm 支持 LIKE '%…%' 与相似度，不依赖分词词典）
CREATE INDEX record_fts_text_trgm ON record_fts USING gin (text gin_trgm_ops);
-- 英文全文匹配（明确配置，避免默认配置随实例 locale 漂移）
CREATE INDEX record_fts_text_en
    ON record_fts USING gin (to_tsvector('english', text));
CREATE INDEX record_fts_kind ON record_fts (project_id, kind);


-- ════════════════════════════════════════════════════════════════════════════
--  不可变历史与结构约束触发器（plpgsql）
--
--  共同语义：
--    * 本体操作/决定是**追加写**的，UPDATE 一律拒绝；
--    * DELETE 只允许在受控维护路径下发生 —— 由会话级 GUC
--      `knowledge.ontology_history_delete_allowed` 表达该开关
--      （见文件头映射说明 7）。GUC 默认未设置 → 视为 0（禁止）。
--    * supersedes 链禁止成环（含自环），用 WITH RECURSIVE 检测。
-- ============================================================================

-- 受控删除开关：默认禁止；维护路径在事务内 SET LOCAL 置 '1'
CREATE OR REPLACE FUNCTION ontology_history_delete_allowed()
RETURNS integer
LANGUAGE sql
STABLE
AS $$
    SELECT COALESCE(
        NULLIF(current_setting('knowledge.ontology_history_delete_allowed', true), ''),
        '0'
    )::integer;
$$;

-- 无条件拒绝（无条件的守卫）
CREATE OR REPLACE FUNCTION reject_ontology_history_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '%', TG_ARGV[0] USING ERRCODE = 'restrict_violation';
END;
$$;

-- 仅当受控删除开关未打开时拒绝（开关为 0 时拒绝）
CREATE OR REPLACE FUNCTION guard_ontology_history_delete()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF ontology_history_delete_allowed() = 0 THEN
        RAISE EXCEPTION '%', TG_ARGV[0] USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN OLD;
END;
$$;

-- 操作：UPDATE 永远拒绝
CREATE TRIGGER ontology_operations_immutable
    BEFORE UPDATE ON ontology_operations
    FOR EACH ROW EXECUTE FUNCTION reject_ontology_history_mutation(
        'ontology operations are immutable');

-- 操作：DELETE 仅维护路径允许
CREATE TRIGGER ontology_operations_delete_immutable
    BEFORE DELETE ON ontology_operations
    FOR EACH ROW EXECUTE FUNCTION guard_ontology_history_delete(
        'ontology operations are immutable');

-- 操作 INSERT：合并两个守卫
--   [A] WHEN NEW.supersedes_operation_id = NEW.id
--         OR EXISTS(SELECT 1 FROM ontology_operations WHERE id = NEW.id)
--       → 主键不可复用 + 禁止自环
--   [B] WHEN supersedes_operation_id IS NOT NULL AND <递归可达自身>
--       → supersedes 链不可成环
CREATE OR REPLACE FUNCTION guard_ontology_operation_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    -- [A]
    IF NEW.supersedes_operation_id = NEW.id
       OR EXISTS (SELECT 1 FROM ontology_operations WHERE id = NEW.id) THEN
        RAISE EXCEPTION 'ontology operations are immutable' USING ERRCODE = 'restrict_violation';
    END IF;

    -- [B] supersedes 链成环检测（自环已在 [A] 排除）
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
        RAISE EXCEPTION 'ontology operation supersession cycle' USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER ontology_operations_insert_immutable
    BEFORE INSERT ON ontology_operations
    FOR EACH ROW EXECUTE FUNCTION guard_ontology_operation_insert();

-- 决定：UPDATE 永远拒绝
CREATE TRIGGER ontology_review_decisions_immutable
    BEFORE UPDATE ON ontology_review_decisions
    FOR EACH ROW EXECUTE FUNCTION reject_ontology_history_mutation(
        'ontology review decisions are immutable');

-- 决定：DELETE 仅维护路径允许
CREATE TRIGGER ontology_review_decisions_delete_immutable
    BEFORE DELETE ON ontology_review_decisions
    FOR EACH ROW EXECUTE FUNCTION guard_ontology_history_delete(
        'ontology review decisions are immutable');

-- 决定 INSERT：合并四个守卫
--   [C] WHEN NOT EXISTS(同 (project,draft,operation_id,fingerprint) 的操作)
--       → 决定必须绑定真实存在的操作指纹
--   [D] WHEN 被替代者 或 替代者的 (operation_id,fingerprint) 与新行不一致
--       → 替代关系必须绑定同一操作
--   [E] WHEN NEW.supersedes_decision_id = NEW.id OR EXISTS(同 id)
--       → 主键不可复用 + 禁止自环
--   [F] WHEN supersedes_decision_id IS NOT NULL AND <递归可达自身>
--       → supersedes 链不可成环
CREATE OR REPLACE FUNCTION guard_ontology_decision_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    -- [C]
    IF NOT EXISTS (
        SELECT 1 FROM ontology_operations AS operation
         WHERE operation.project_id = NEW.project_id
           AND operation.draft_id = NEW.draft_id
           AND operation.id = NEW.operation_id
           AND operation.fingerprint = NEW.operation_fingerprint
    ) THEN
        RAISE EXCEPTION 'ontology decision operation fingerprint mismatch'
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- [D]
    IF EXISTS (
        SELECT 1 FROM ontology_review_decisions AS prior
         WHERE prior.project_id = NEW.project_id
           AND prior.draft_id = NEW.draft_id
           AND prior.id = NEW.supersedes_decision_id
           AND (prior.operation_id <> NEW.operation_id
                OR prior.operation_fingerprint <> NEW.operation_fingerprint)
    ) OR EXISTS (
        SELECT 1 FROM ontology_review_decisions AS replacement
         WHERE replacement.project_id = NEW.project_id
           AND replacement.draft_id = NEW.draft_id
           AND replacement.supersedes_decision_id = NEW.id
           AND (replacement.operation_id <> NEW.operation_id
                OR replacement.operation_fingerprint <> NEW.operation_fingerprint)
    ) THEN
        RAISE EXCEPTION 'superseded decisions must bind to the same operation'
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- [E]
    IF NEW.supersedes_decision_id = NEW.id
       OR EXISTS (SELECT 1 FROM ontology_review_decisions WHERE id = NEW.id) THEN
        RAISE EXCEPTION 'ontology review decisions are immutable'
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- [F]
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
        RAISE EXCEPTION 'ontology review decision supersession cycle'
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER ontology_review_decisions_insert_immutable
    BEFORE INSERT ON ontology_review_decisions
    FOR EACH ROW EXECUTE FUNCTION guard_ontology_decision_insert();

-- 历史修复台账 INSERT
--   条件为 EXISTS(同 repair_key) —— **只有键已存在才拒绝**，
--   新键必须允许写入。若写成无条件拒绝，台账将永远无法追加。
CREATE OR REPLACE FUNCTION guard_ontology_repair_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM ontology_history_repairs
                WHERE repair_key = NEW.repair_key) THEN
        RAISE EXCEPTION 'ontology history repair records are immutable'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER ontology_history_repairs_insert_immutable
    BEFORE INSERT ON ontology_history_repairs
    FOR EACH ROW EXECUTE FUNCTION guard_ontology_repair_insert();

-- 历史修复台账：UPDATE / DELETE 一律拒绝（无条件）
CREATE TRIGGER ontology_history_repairs_update_immutable
    BEFORE UPDATE ON ontology_history_repairs
    FOR EACH ROW EXECUTE FUNCTION reject_ontology_history_mutation(
        'ontology history repair records are immutable');

CREATE TRIGGER ontology_history_repairs_delete_immutable
    BEFORE DELETE ON ontology_history_repairs
    FOR EACH ROW EXECUTE FUNCTION reject_ontology_history_mutation(
        'ontology history repair records are immutable');


-- ════════════════════════════════════════════════════════════════════════════
--  应用角色授权（幂等；角色由 docker/postgres/initdb/10-app-role.sh 创建）
--
--  原则（规划 §5.1）：应用角色可读写 DML，但**无 schema DDL**。
--  含 DEFAULT PRIVILEGES，保证迁移之后新建的表也自动授权，
--  避免「迁移后应用突然无权限」这类只在生产暴露的问题。
-- ════════════════════════════════════════════════════════════════════════════

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'knowledge_app') THEN
        GRANT USAGE ON SCHEMA public TO knowledge_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO knowledge_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO knowledge_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO knowledge_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT USAGE, SELECT ON SEQUENCES TO knowledge_app;
        -- 明确不授予 CREATE（无 DDL）：可被验证脚本断言
        REVOKE CREATE ON SCHEMA public FROM knowledge_app;
    ELSE
        RAISE NOTICE '应用角色 knowledge_app 不存在，跳过授权（请先执行 initdb 脚本）';
    END IF;
END;
$$;
