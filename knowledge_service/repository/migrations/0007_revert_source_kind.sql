-- ════════════════════════════════════════════════════════════════════════════
--  0007：草案来源新增 'revert'（版本管理的「回到某一版」）
--
--  问题
--    0001_core.sql:341 把 `ontology_drafts.source_kind` 的合法取值写死在 CHECK 里
--    （manual / turtle / import / ai / discovery / candidate）。版本管理要做
--    「回到某一版」—— 以**历史版本**为基线开一份新草案，发布后成为新版本
--    （历史不改写）。这种草案的来源与上面六种都不同，它要表达的是
--    「基线故意不是最新版」这个显式意图。
--
--  为什么不能复用 manual
--    `services/ontology_drafts.create()` 对普通来源有一条保护：基线必须是项目当前
--    版本（stale_base 409）。这条保护是对的 —— 在旧基线上做**普通编辑**会静默丢掉
--    别人后来的改动。但「我就是要回到 v2」是另一件事：它有明确的用户意图，并且
--    发布时留下版本记录。所以来源必须能区分这两种情况，不能拿 manual 混过去。
--
--  语义（代码侧对应 REVERT_SOURCE / REVERT_WARNING）
--    ① 允许（也要求）以本项目内**已存在**的某个版本为基线；
--    ② 发布门禁要求显式确认 revert_drops_later_versions —— 回退会让当前版本回到
--       那一版的结构，它之后发布的版本里的结构改动不再留在当前版本里
--       （那些版本本身仍在版本管理里可查、可再回去）；
--    ③ 这类草案不再算「需重新基线」（needs_rebase 返回 None）—— 让人去 rebase
--       等于把回退意图直接抹掉。
--
--  幂等
--    约束先 DROP IF EXISTS 再 ADD，重复执行结果一致；纯约束变更，不触碰数据，
--    因此不需要数据搬迁，也不需要顺序技巧（对比 0005 的 DROP→UPDATE→ADD）。
--
--  反向 SQL（仅在"迁移已跑、但代码还没上"时用来救急，留档备查）
--    先确认没有 revert 来源的草案，再退回旧 CHECK：
--        SELECT count(*) FROM ontology_drafts WHERE source_kind = 'revert';
--        ALTER TABLE ontology_drafts DROP CONSTRAINT IF EXISTS ontology_drafts_source_kind_check;
--        ALTER TABLE ontology_drafts ADD CONSTRAINT ontology_drafts_source_kind_check
--            CHECK (source_kind IN ('manual','turtle','import','ai','discovery','candidate'));
--    若计数不为 0，DROP 之前先把那些行改掉或删掉，否则新 CHECK 会拒绝已有数据。
-- ════════════════════════════════════════════════════════════════════════════

ALTER TABLE ontology_drafts DROP CONSTRAINT IF EXISTS ontology_drafts_source_kind_check;

ALTER TABLE ontology_drafts ADD CONSTRAINT ontology_drafts_source_kind_check
    CHECK (source_kind IN ('manual', 'turtle', 'import', 'ai', 'discovery',
                           'candidate', 'revert'));

-- 回退草案要能一眼看出"回到的是哪一版"，而不用去翻 base_ontology_id 里那串 uuid。
-- 它在 source_context（JSON 文本）里，本迁移只给"按来源查"加一个索引：
-- 版本管理列表要按 project + source_kind 找这类草案。
CREATE INDEX IF NOT EXISTS ontology_drafts_source_kind_idx
    ON ontology_drafts (project_id, source_kind);
