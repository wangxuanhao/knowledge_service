# -*- coding: utf-8 -*-
"""0006 回填演练（A3 的补丁迁移）：在**真库上跑一遍 0006 的语句并回滚**，证明它只改该改的行。

为什么需要演练（而不是只看 0005 的演练）：
    0006 是**纯数据迁移**（不改 schema），它的全部正确性都在那条 UPDATE 的 WHERE 上：
      * 该回填的（pending + 有独立证据 + submitted_at 为空）必须被填上；
      * 不该动的（拿不出证据的 pending、终态 accepted / rejected、已经有值的行）必须一行不改。
    真库上确实存在"迁移前处于 submitted / reviewed"的那批行，但**不能拿它们做实验**
    （那是用户的真实数据）。所以在同一个库里现造样本：插行 → 跑语句 → 逐条断言 → ROLLBACK，
    跑完真库一条样本都不该留下（脚本自己核对行数）。

演练覆盖不到的一支（诚实说明）：
    WHERE 里 `EXISTS(决定行)` 这一支需要造出一条合法的审核决定 ——
    决定表有不可变触发器 + 指向 ontology_operations 的外键，构造合法样本等于把审核流程
    重走一遍，成本远高于收益。这一支改用**真机数据**取证（见落地进度.md 的 A3 记录）：
    真库 07b8563f… 是 pending + 20 条决定 + validated_at 为空，本迁移执行后它被回填，
    probe 复查时它已不在"有证据却为空"的名单里。

安全：全程不打印连接串（owner DSN 经 diagnostics.redact）。
用法：python scripts/rehearse_migration_0006.py
"""
import sys

sys.path.insert(0, r'D:\workspace\knowledge_service')

from knowledge_service.core.config import load_environment  # noqa: E402

# 与 rehearsal_0005 同因：本脚本插样本/回滚用的是真库，走 owner 凭据。
load_environment(include_admin=True)

import psycopg  # noqa: E402

from knowledge_service.repository.migrate import MIGRATIONS_DIR, resolve_migration_dsn  # noqa: E402
from knowledge_service.utils.diagnostics import redact  # noqa: E402

MIGRATION = MIGRATIONS_DIR / '0006_backfill_draft_submitted_at.sql'
SAMPLE = '0006-rehearsal'
checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))
    print(f'{"PASS" if ok else "FAIL"} · {name}' + (f' · {detail}' if detail else ''))


dsn = resolve_migration_dsn()
print('dsn(owner):', redact(dsn))
sql = MIGRATION.read_text(encoding='utf-8')

# 样本：id 后缀 → (status, validated_at, 预置 submitted_at, 期望的 submitted_at)
#   'evidence'   pending + 有校验快照            → 回填成 validated_at
#   'no-evidence' pending + 什么证据都没有        → 保持 NULL（宁可少改，不猜）
#   'already'    pending + 已经有提交时刻          → 保持原值（不被覆盖）
#   'accepted'   终态已收下 + 有校验快照           → 不动（作用域只收 pending）
#   'rejected'   终态已驳回 + 有校验快照           → 不动
SAMPLES = ('evidence', 'no-evidence', 'already', 'accepted', 'rejected')


class _RollbackExpected(Exception):
    """内部信号：样本验完就抛它，让这一整个事务回滚 —— 这就是本脚本的收尾动作。"""


# ① 只读探测：拿一个真实项目 id + 演练前的草案总数
with psycopg.connect(dsn) as conn:
    with conn.cursor() as cur:
        cur.execute('SELECT id FROM projects LIMIT 1')
        row = cur.fetchone()
        if row is None:
            raise SystemExit('真库里没有项目，无法演练（先建一个项目再跑）')
        project_id = row[0]
        cur.execute('SELECT count(*) FROM ontology_drafts')
        before_total = cur.fetchone()[0]
    conn.rollback()

# ② 演练事务：无论成功失败都不提交（成功路径靠 _RollbackExpected 回滚）
try:
    with psycopg.connect(dsn) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                for name in SAMPLES:
                    status = {'accepted': 'accepted', 'rejected': 'rejected'}.get(name, 'pending')
                    validated = 'NULL' if name == 'no-evidence' else 'now()'
                    submitted = "now() - interval '1 day'" if name == 'already' else 'NULL'
                    cur.execute(
                        f'''INSERT INTO ontology_drafts
                            (id, project_id, base_ontology_id, source_kind, status, revision,
                             title, summary, source_context, validated_at, submitted_at,
                             created_at, updated_at)
                            VALUES (%s,%s,NULL,'manual',%s,1,'演练','演练','{{}}',
                                    {validated},{submitted},now(),now())''',
                        (f'{SAMPLE}-{name}', project_id, status))

                cur.execute(sql)
                cur.execute(
                    """SELECT id, submitted_at, validated_at FROM ontology_drafts
                        WHERE id LIKE %s""", (f'{SAMPLE}-%',))
                got = {row_id.replace(f'{SAMPLE}-', ''): (submitted_at, validated_at)
                       for row_id, submitted_at, validated_at in cur.fetchall()}

                check('「有校验快照的 pending」被回填，且取值 = validated_at',
                      got.get('evidence', (None, None))[0] is not None
                      and got['evidence'][0] == got['evidence'][1],
                      f"submitted_at={got.get('evidence', (None,))[0]}")
                check('「拿不出证据的 pending」保持为空（不猜）',
                      got.get('no-evidence', (None, None))[0] is None,
                      f"submitted_at={got.get('no-evidence', (None,))[0]}")
                check('「已经有提交时刻的行」不被覆盖',
                      got.get('already', (None, None))[0] is not None
                      and got['already'][0] < got['already'][1],
                      f"submitted_at={got.get('already', (None,))[0]}")
                check('终态行（accepted / rejected）一行不改',
                      got.get('accepted', (None, None))[0] is None
                      and got.get('rejected', (None, None))[0] is None,
                      f"accepted={got.get('accepted', (None,))[0]} · "
                      f"rejected={got.get('rejected', (None,))[0]}")

                # 幂等：同一套语句再跑一次，值与行数都不变
                before_rerun = {key: value[0] for key, value in got.items()}
                cur.execute(sql)
                cur.execute(
                    """SELECT id, submitted_at FROM ontology_drafts WHERE id LIKE %s""",
                    (f'{SAMPLE}-%',))
                after_rerun = {row_id.replace(f'{SAMPLE}-', ''): value
                               for row_id, value in cur.fetchall()}
                check('重复执行幂等（第二次不再改任何行）', before_rerun == after_rerun,
                      f'{after_rerun}')

                # 收尾由事务回滚完成（下面的 raise 是刻意的：不提交就是本脚本的"回滚"）。
                raise _RollbackExpected()
except _RollbackExpected:
    pass

# ③ 回滚之后：真库必须与演练前逐行一致
with psycopg.connect(dsn) as conn:
    with conn.cursor() as cur:
        cur.execute('SELECT count(*) FROM ontology_drafts')
        after_total = cur.fetchone()[0]
check('演练行已随事务回滚清干净', after_total == before_total,
      f'{before_total} → {after_total}')

failed = [name for name, ok, _ in checks if not ok]
print(f'\n共 {len(checks)} 项：通过 {len(checks) - len(failed)}，失败 {len(failed)}')
if failed:
    print('失败项：', failed)
    raise SystemExit(1)
print('0006 回填演练通过（真库数据未被改动）。')
