-- ════════════════════════════════════════════════════════════════════════════
--  0005：草案状态 7 → 3（A3「状态机退场」）
--
--  问题
--    `ontology_drafts.status` 有 7 个枚举（editing / submitted / reviewed /
--    published / closed / stale_base / stale_source，0001_core.sql:343 的 CHECK
--    写死）。它把三个互不相干的轴挤进同一条状态条：
--      ① 编辑进度（我还没点保存 = editing）
--      ② 别人的请求走到哪一步（submitted / reviewed）
--      ③ 原文/基线过期（stale_base / stale_source）
--    用户看到的是「审核台一条状态条上四个阶段互相锁」以及"环节冲突"。
--
--  目标（评审文档 §4.3）
--    * **本体没有状态** —— 只有版本号 v1..vN。「已发布」不是状态，是它唯一的
--      存在方式；所以 `published` 这个状态本身就该消失。
--    * **请求只有三种状态**：待处理 → 已收下 / 已驳回。
--    * 「需重新基线」不是状态，是**推导**出来的事实（base_ontology_id 落后于当前
--      本体，或来源快照对不上）—— 落成状态就会出现"状态说没过期、实际已过期"
--      这种两套口径打架的问题（见 0004 的同类教训）。
--
--  映射（不可逆：旧的四档合并进 pending 后无法还原，故本迁移前必须备份）
--    editing / submitted / reviewed / stale_base / stale_source → pending
--    published                                                 → accepted
--    closed                                                    → rejected
--
--  为什么这个迁移不能回滚（诚实说明）
--    迁移动器（repository/migrate.py）只支持 up。把 5 档合并成 pending 是**有损**的，
--    所以真正的 down 也做不到"还原原状态"，只能退回旧 CHECK —— 那样更危险
--    （旧 CHECK 会拒绝已经写进去的 pending）。回滚的正确做法是：
--        pg_dump 备份 → 回滚代码 → 从备份恢复表数据。
--    反向 SQL（仅在"迁移已跑、但代码还没上"时用来救急，留档备查）：
--        UPDATE ontology_drafts SET status='published' WHERE status='accepted';
--        UPDATE ontology_drafts SET status='closed'    WHERE status='rejected';
--        UPDATE ontology_drafts SET status='editing'   WHERE status='pending';
--        ALTER TABLE ontology_drafts DROP CONSTRAINT ontology_drafts_status_check;
--        ALTER TABLE ontology_drafts ADD CONSTRAINT ontology_drafts_status_check
--            CHECK (status IN ('editing','submitted','reviewed','published',
--                              'closed','stale_base','stale_source'));
--
--  顺带补一列 submitted_at
--    旧状态里 `submitted` 同时承担了两件事：① 这份请求已经提交（冻结快照）；
--    ② 它排在「待审核」。状态收成三种之后 ① 需要一个新的落点 —— 而且它本来就该是
--    **时间戳**（"什么时候提交的"）而不是状态（同 0004 给 validated_at 的理由）。
--    语义：submit() 写当前时间；**任何一次改动**（command / rebase / 刷新来源副作用 /
--    退回继续编辑）都清空它 —— 改了东西就等于撤回了这次提交。
--    为什么不用 validated_at 顶替：提交本身会故意清空 validated_at（重新提交即作废
--    上一次校验），拿它当"提交过没有"会让刚提交的请求被当成没提交。

--  幂等
--    两条 UPDATE 覆盖所有旧值、对新值无副作用；约束先 DROP IF EXISTS 再 ADD，
--    因此本文件重复执行结果一致。
-- ════════════════════════════════════════════════════════════════════════════

-- ① 先摘掉旧 CHECK。**顺序必须是这样**：老数据的 UPDATE 就是被这条 CHECK 拦下的
--    （演练第一版把 DROP 写在 UPDATE 之后，真库上直接 CheckViolation：
--     `new row ... violates check constraint "ontology_drafts_status_check"`），
--    所以只能先放开约束、改数据、再把新约束装回去。
ALTER TABLE ontology_drafts DROP CONSTRAINT IF EXISTS ontology_drafts_status_check;

-- ② 把老数据搬到新词汇上（顺序无关：每条只匹配自己的旧值）
UPDATE ontology_drafts SET status = 'pending'
 WHERE status IN ('editing', 'submitted', 'reviewed', 'stale_base', 'stale_source');
UPDATE ontology_drafts SET status = 'accepted' WHERE status = 'published';
UPDATE ontology_drafts SET status = 'rejected' WHERE status = 'closed';

-- ③ 装上只留三种请求状态的新 CHECK
ALTER TABLE ontology_drafts ADD CONSTRAINT ontology_drafts_status_check
    CHECK (status IN ('pending', 'accepted', 'rejected'));

-- ④ 「已提交」的落点：时间戳（可空）
ALTER TABLE ontology_drafts ADD COLUMN IF NOT EXISTS submitted_at timestamptz;
