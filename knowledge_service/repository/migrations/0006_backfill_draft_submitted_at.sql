-- ════════════════════════════════════════════════════════════════════════════
--  0006：回填 submitted_at —— 补上 0005 那笔**有损迁移**留下的一个洞
--
--  0005（状态 7 → 3）把 editing / submitted / reviewed / stale_base /
--  stale_source 五档**合并**成 pending，同时新增 submitted_at 记「提交时刻」。
--  但它只做了映射、**没有回填**：迁移前处于 submitted / reviewed 的请求，
--  迁移后 status='pending' 而 submitted_at 为 NULL。
--
--  后果不是"少一个时间戳"，而是**文案撒谎**：
--  services/ontology_drafts.pending_phase() 用 submitted_at 回答"这份请求提交过没有"，
--  为空即 'editing' → 审核台收件箱（web/ontology-workbench.js 的 renderReview）
--  判定 pending_phase==='editing' 时**整条逐项收下队列都不渲染**，只留一张
--  「去本体编辑台改」的指路卡。于是一份**已经提交、正在审核、甚至已经逐条决定过**
--  的请求，被重新丢回编辑台 —— 用户看到的是"我明明提交了，它说我还没提交"。
--
--  真机读数（本迁移执行前，真库 knowledge）：
--      ontology_drafts 共 25 份：accepted 5 · pending 16 · rejected 4
--      其中 pending 且 submitted_at IS NULL、但**有独立证据证明提交过**的有 2 份：
--        07b8563f…  20 条决定 / 17 条变更 · validated_at 为空
--        bc449749…   0 条决定 /  1 条变更 · validated_at 非空
--      （另有 5 份 accepted 也符合证据判据，见下面的作用域说明）
--
--  判据（只用**已经独立存在的证据**，绝不靠"以前可能是那个状态"来猜）：
--      ① status = 'pending'（终态行不参与 pending_phase，见下）；
--      ② 存在审核决定行 或 校验快照非空 —— 这两件事都只可能发生在提交之后
--         （提交 → 校验 → 逐条决定）。
--
--  取值：COALESCE(validated_at, updated_at)
--      提交时刻本身**不可恢复**（0005 已经把状态词抹平了，这是有损的一部分），
--      所以取"最近一次可证据化的动作时间"作为近似，并在注释里说清它是近似值。
--      pending_phase() 只关心**有没有值**，不读它的绝对值，所以近似不影响判定。
--
--  作用域为什么只收 pending
--      终态（accepted / rejected）的 pending_phase() 返回 None，没有任何读处；
--      给它们补一个可能不准的时间戳，等于**改历史账本**而不带来任何行为改善。
--      真要读"这份请求什么时候提交的"，正确做法是那次审核的决定记录（时间更准）。
--      所以这里刻意窄：只修会被人读到、且读错会骗人的那一批。
--
--  诚实边界（没能覆盖的）
--      有极少数行**提交过但拿不出任何证据**：提交后没有跑过校验、也没有任何决定，
--      且变更条数不构成证据。这种行无法与"一直在编辑中"区分，本迁移不动它们 ——
--      它们会显示成 pending_phase='editing'（去编辑台）。宁可少改，不猜。
--
--  幂等
--      WHERE submitted_at IS NULL ⇒ 重复执行不再命中任何行（第二次是 0 行更新）。
--      本迁移**不改 schema**：列已在 0005 里加好，所以快照行形状与格式版本 17 不变。
-- ════════════════════════════════════════════════════════════════════════════

UPDATE ontology_drafts AS d
   SET submitted_at = COALESCE(d.validated_at, d.updated_at)
 WHERE d.submitted_at IS NULL
   AND d.status = 'pending'
   AND (d.validated_at IS NOT NULL
        OR EXISTS (SELECT 1 FROM ontology_review_decisions dd
                    WHERE dd.project_id = d.project_id AND dd.draft_id = d.id));
