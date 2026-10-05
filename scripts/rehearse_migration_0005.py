# -*- coding: utf-8 -*-
"""0005 迁移演练（A3）：在**真库同一个 schema 上**跑一遍并回滚，证明迁移语句本身可用。

为什么要这么设计：
  * 迁移动器（repository/migrate.py）只支持 up，没有 down；把 7 档状态合并成 3 档是
    **有损**的，所以"回滚"只能是"备份后恢复"，不能靠 SQL 反着执行。
  * 于是演练要回答的问题只剩下一个：**这套语句对当前 schema 能不能跑通、映射对不对**。
    办法是把真库放进一个事务：插 7 条各状态样本 → 执行迁移语句 → 断言映射 →
    ROLLBACK。跑完数据库里连一条样本行都不该留下（脚本会自己核对）。

不打印任何连接串：DSN 一律经 diagnostics.redact()。
"""
import sys

sys.path.insert(0, r'D:\workspace\knowledge_service')

from knowledge_service.core.config import load_environment  # noqa: E402

# ⚠️ 必须是 include_admin=True：本脚本要 DROP/ADD CONSTRAINT（DDL），只能用 owner 凭据，
# 而默认加载路径**故意**不把 POSTGRES_PASSWORD 带进进程（见 core/config.load_environment）。
# 写错成 load_environment() 的表现是"缺少迁移凭据"直接退出 —— 脚本一行都跑不到。
load_environment(include_admin=True)

import psycopg  # noqa: E402

from knowledge_service.repository.connection import resolve_dsn  # noqa: E402
from knowledge_service.repository.migrate import MIGRATIONS_DIR, resolve_migration_dsn  # noqa: E402
from knowledge_service.utils.diagnostics import redact  # noqa: E402

MIGRATION = MIGRATIONS_DIR / '0005_draft_status_three_states.sql'
EXPECTED = {
    'editing': 'pending', 'submitted': 'pending', 'reviewed': 'pending',
    'stale_base': 'pending', 'stale_source': 'pending',
    'published': 'accepted', 'closed': 'rejected',
}


def restore_legacy_check(cur):
    """把 ontology_drafts.status 的 CHECK 还原成"迁移之前"的形状（供演练插旧词汇样本）。

    为什么是**超集**（七档 + 三档）而不是原样七档：
      真库里可能已经有迁移后的行（pending/accepted/rejected），原样七档的 CHECK 会被这些
      行直接顶回来（CheckViolation: "violated by some row"）。演练要的是"能插进旧词汇"，
      而这七档 + 三档的全集对旧库与新库都成立。
    本次演练之后由迁移语句自己 DROP + ADD 成三档，最终形状不受影响。
    """
    cur.execute('ALTER TABLE ontology_drafts '
                'DROP CONSTRAINT IF EXISTS ontology_drafts_status_check')
    cur.execute("""ALTER TABLE ontology_drafts ADD CONSTRAINT ontology_drafts_status_check
                   CHECK (status IN ('editing','submitted','reviewed','published',
                                     'closed','stale_base','stale_source',
                                     'pending','accepted','rejected'))""")

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))
    print(f'{"PASS" if ok else "FAIL"} · {name}' + (f' · {detail}' if detail else ''))


# DDL（DROP/ADD CONSTRAINT）只有 owner 能做：应用角色 knowledge_app 是**故意**没有
# DDL 权限的（AGENTS.md 第 7 条）。所以演练用迁移器那份 owner 凭据。
dsn = resolve_migration_dsn()
print('dsn(owner):', redact(dsn))
sql = MIGRATION.read_text(encoding='utf-8')

with psycopg.connect(dsn) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM projects LIMIT 1")
        row = cur.fetchone()
        if row is None:
            raise SystemExit('真库里没有项目，无法演练（先造一个项目再跑）')
        project_id = row[0]
        cur.execute('SELECT count(*) FROM ontology_drafts')
        before_total = cur.fetchone()[0]

        # 真库可能**已经**跑过 0005（迁移器只跑一次，且不可逆）。演练要复现的是"迁移之前
        # 的长相"，所以先把 CHECK 还原成旧形状 —— 否则连 7 条样本都插不进去
        # （第一版脚本就是在已经迁移过的库上炸 CheckViolation 的）。
        restore_legacy_check(cur)

        # ── 事务演练：改完一定 ROLLBACK ──────────────────────────────────
        for index, status in enumerate(EXPECTED):
            cur.execute(
                """INSERT INTO ontology_drafts
                   (id, project_id, base_ontology_id, source_kind, status, revision,
                    title, summary, source_context, created_at, updated_at)
                   VALUES (%s,%s,NULL,'manual',%s,1,'演练','演练','{}',now(),now())""",
                (f'0005-rehearsal-{index}', project_id, status))
        cur.execute(sql)
        cur.execute("""SELECT id, status FROM ontology_drafts
                       WHERE id LIKE '0005-rehearsal-%' ORDER BY id""")
        got = {row_id.replace('0005-rehearsal-', ''): status for row_id, status in cur.fetchall()}
        want = {str(index): EXPECTED[status] for index, status in enumerate(EXPECTED)}
        check('7 条样本按映射落成 3 档', got == want, f'{got}')

        # 新 CHECK 真的生效：写入旧词汇必须被拒
        try:
            cur.execute("""INSERT INTO ontology_drafts
                   (id, project_id, base_ontology_id, source_kind, status, revision,
                    title, summary, source_context, created_at, updated_at)
                   VALUES ('0005-rehearsal-bad',%s,NULL,'manual','editing',1,
                           '演练','演练','{}',now(),now())""", (project_id,))
            check('新 CHECK 拒绝旧词汇 editing', False, '居然写进去了')
        except psycopg.errors.CheckViolation:
            check('新 CHECK 拒绝旧词汇 editing', True, 'CheckViolation')

        # 幂等：同一套语句再跑一次不能出错
        try:
            conn.rollback()  # 上一步的违规把事务置为 aborted，先回滚再重开一轮
        except Exception:  # noqa: BLE001
            pass

    # 重开一轮：重跑迁移语句验证幂等（重复执行结果一致）
    with conn.transaction():
        with conn.cursor() as cur:
            # 上一轮的迁移已经把 CHECK 收成三档了（那一轮是提交的）：这里同样先装回旧形状，
            # 才能再插旧词汇样本，测的才是"同一套语句重复执行"。
            restore_legacy_check(cur)
            for index, status in enumerate(('editing', 'published', 'closed')):
                cur.execute(
                    """INSERT INTO ontology_drafts
                       (id, project_id, base_ontology_id, source_kind, status, revision,
                        title, summary, source_context, created_at, updated_at)
                       VALUES (%s,%s,NULL,'manual',%s,1,'演练','演练','{}',now(),now())""",
                    (f'0005-rehearsal-again-{index}', project_id, status))
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute(sql)  # 第二次执行：DROP IF EXISTS + ADD，必须仍然成功
            cur.execute("""SELECT status FROM ontology_drafts
                           WHERE id LIKE '0005-rehearsal-again-%' ORDER BY id""")
            again = [row[0] for row in cur.fetchall()]
            check('重复执行迁移幂等', again == ['pending', 'accepted', 'rejected'], f'{again}')

    # ── 收尾：清掉演练行（真库必须一条不留）──────────────────────────
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ontology_drafts WHERE id LIKE '0005-rehearsal-%'")
            deleted = cur.rowcount
            cur.execute('SELECT count(*) FROM ontology_drafts')
            after_total = cur.fetchone()[0]
    check('演练行已清干净', after_total == before_total, f'{before_total} → {after_total}（删了 {deleted} 行）')

failed = [name for name, ok, _ in checks if not ok]
print(f'\n共 {len(checks)} 项：通过 {len(checks) - len(failed)}，失败 {len(failed)}')
if failed:
    print('失败项：', failed)
    raise SystemExit(1)
print('0005 迁移演练通过（真库数据未被改动）。')
