"""PostgreSQL schema 契约与行为验收。

**默认跳过**：需要真实的 PostgreSQL。设置以下环境变量后才会执行 ——

    KG_TEST_POSTGRES_DSN      迁移角色/owner DSN（用于建临时对象与断言权限）
    KG_TEST_POSTGRES_APP_DSN  应用角色 DSN（用于断言「可 DML、不可 DDL」）

跳过而非失败，是为了让 `pytest` 在没有数据库的机器上仍然全绿；需要真库的
验收不该污染普通单测（规划 §5.1 的分层原则）。CI 里显式提供 DSN 即生效。

验收口径——**以 PostgreSQL 自身为基准**：

  * 结构：必须存在的业务表、每表必须有主键、关键业务唯一约束、
    插入顺序列（IDENTITY）及其索引。
  * 行为：不可变触发器对 INSERT/UPDATE/DELETE 的**接受与拒绝集合**
    （不比对触发器文本，只比对可观察行为）。
  * 权限：应用角色可 DML、不可 DDL（规划 §5.1 的最小权限）。

本文件不引用任何外部基线：项目是纯 PostgreSQL 实现，schema 的期望值直接来自
迁移脚本自身（repository/migrations/），并在此显式登记，便于评审与改动追溯。
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest

psycopg = pytest.importorskip('psycopg')

OWNER_DSN = os.environ.get('KG_TEST_POSTGRES_DSN', '').strip()
APP_DSN = os.environ.get('KG_TEST_POSTGRES_APP_DSN', '').strip()

pytestmark = pytest.mark.skipif(
    not OWNER_DSN,
    reason='未设置 KG_TEST_POSTGRES_DSN，跳过需要真实 PostgreSQL 的 schema 验收',
)

# 迁移后必须存在的业务表（0001_core.sql 的 22 张 + 迁移器台账表）。
REQUIRED_TABLES = frozenset({
    'projects', 'record_versions', 'ontologies', 'artifacts', 'service_settings',
    'assertions', 'assertion_events', 'fact_keys', 'ingest_runs',
    'ingest_stage_outputs', 'merge_operations', 'resolution_reviews',
    'record_operation_reservations', 'provenance_activities', 'provenance_edges',
    'record_version_assertions', 'ontology_drafts', 'ontology_operations',
    'ontology_review_decisions', 'ontology_publish_requests',
    'ontology_history_repairs', 'record_fts', 'schema_migrations',
})

# 0002 迁移显式补出的插入顺序列：业务按「插入顺序」读这些表。
# 每张表还必须有 (project_id, .., seq) 索引，否则 ORDER BY seq 要整表排序。
INSERTION_ORDER_TABLES = (
    'artifacts', 'ontologies', 'assertion_events', 'ontology_drafts',
    'ontology_operations', 'ontology_review_decisions', 'merge_operations',
)

# 关键业务唯一约束：(表 -> 必须存在的唯一列组合)。顺序无关，故用 frozenset。
# 这些是业务正确性的前提：例如 record_versions 的「当前版本」唯一、
# provenance_edges 的 ordinal 去重键、ontology_operations 的
# (project_id,draft_id,id) 防止跨项目/跨草案的 id 冲突。
KEY_UNIQUE_CONSTRAINTS = {
    'record_versions': {frozenset({'project_id', 'id'})},                    # versions_current
    'fact_keys': {frozenset({'project_id', 'fact_key', 'created_at'})},
    'ingest_runs': {frozenset({'document_version_id', 'attempt'})},
    'ingest_stage_outputs': {
        frozenset({'document_version_id', 'chunk_id', 'stage', 'input_hash'})},
    'record_operation_reservations': {frozenset({'project_id', 'recorded_at'})},
    'provenance_activities': {frozenset({'project_id', 'id'})},
    'provenance_edges': {
        frozenset({'project_id', 'activity_id', 'source_ref', 'relation',
                   'target_ref', 'ordinal'})},
    'record_version_assertions': {
        frozenset({'project_id', 'record_version_id', 'assertion_id',
                   'assertion_event_id'})},
    'ontology_drafts': {frozenset({'project_id', 'id'})},
    'ontology_operations': {frozenset({'project_id', 'draft_id', 'id'})},
    'ontology_review_decisions': {frozenset({'project_id', 'draft_id', 'id'})},
    'ontology_publish_requests': {
        frozenset({'project_id', 'draft_id', 'idempotency_key'})},
    'record_fts': {frozenset({'project_id', 'record_id'})},
    'schema_migrations': {frozenset({'version'})},
}

# 有意**不设主键**、只用唯一约束表达行身份的表：它们是派生/关联表，
# 主键列组合与业务唯一键并不一致，用 UNIQUE 表达更贴近语义。
# 见 test_every_table_has_a_candidate_key 的断言。
TABLES_WITHOUT_PRIMARY_KEY = frozenset({
    'provenance_activities', 'record_version_assertions',
})


@pytest.fixture(scope='module')
def owner_conn():
    conn = psycopg.connect(OWNER_DSN, autocommit=True)
    yield conn
    conn.close()


def _actual_tables(conn):
    with conn.cursor() as cur:
        cur.execute("""select c.relname from pg_class c
                         join pg_namespace n on n.oid = c.relnamespace
                        where n.nspname = 'public' and c.relkind = 'r'""")
        return {r[0] for r in cur.fetchall()}


def _unique_key_sets(conn, table):
    """该表上所有唯一约束的列组合（顺序无关）。"""
    with conn.cursor() as cur:
        cur.execute("""
            select array_agg(a.attname)
              from pg_index i
              join pg_class t on t.oid = i.indrelid
              join pg_namespace n on n.oid = t.relnamespace
              join unnest(i.indkey) as k(attnum) on true
              join pg_attribute a on a.attrelid = t.oid and a.attnum = k.attnum
             where n.nspname = 'public' and t.relname = %s and i.indisunique
             group by i.indexrelid
        """, (table,))
        return {frozenset(r[0]) for r in cur.fetchall()}


def _index_key_sets(conn):
    """{(表, 列组合)} —— 用于确认 (project_id, .., seq) 索引确实存在。"""
    with conn.cursor() as cur:
        cur.execute("""
            select t.relname, array_agg(a.attname order by k.ord)
              from pg_index i
              join pg_class t on t.oid = i.indrelid
              join pg_namespace n on n.oid = t.relnamespace
              join unnest(i.indkey) with ordinality as k(attnum, ord) on true
              join pg_attribute a on a.attrelid = t.oid and a.attnum = k.attnum
             where n.nspname = 'public' and a.attnum > 0
             group by t.relname, i.indexrelid
        """)
        return {(r[0], tuple(r[1])) for r in cur.fetchall()}


# ── 结构：表 / 主键 / 唯一约束 / 插入顺序列 ─────────────────────────────────

def test_required_business_tables_exist(owner_conn):
    """所有业务表都必须存在（含迁移器台账表 schema_migrations）。"""
    missing = sorted(REQUIRED_TABLES - _actual_tables(owner_conn))
    assert not missing, f'缺失业务表：{missing}'


def test_every_table_has_a_candidate_key(owner_conn):
    """每张表都必须有行身份：主键，或有意为之的唯一约束（候选键）。

    无候选键的表无法可靠定位单行，也无法被外键引用 —— 这是数据完整性的前提。
    """
    with owner_conn.cursor() as cur:
        cur.execute("""
            select c.relname,
                   exists (select 1 from pg_index i
                            where i.indrelid = c.oid and i.indisprimary) as has_pk,
                   exists (select 1 from pg_index i
                            where i.indrelid = c.oid and i.indisunique) as has_unique
              from pg_class c
              join pg_namespace n on n.oid = c.relnamespace
             where n.nspname = 'public' and c.relkind = 'r'
             order by c.relname
        """)
        rows = cur.fetchall()

    without_key = [n for n, pk, uniq in rows if not (pk or uniq)]
    assert not without_key, f'既无主键也无唯一约束的表：{without_key}'

    # 反向守住「有意不设主键」的清单：既不允许悄悄新增裸表，
    # 也不允许清单长期不更新（补了主键就该从清单里移除）。
    actual_without_pk = {n for n, pk, _ in rows if not pk}
    assert actual_without_pk == set(TABLES_WITHOUT_PRIMARY_KEY), (
        f'无主键的表与登记不符：实际 {sorted(actual_without_pk)} '
        f'vs 登记 {sorted(TABLES_WITHOUT_PRIMARY_KEY)}')


def test_key_unique_constraints_exist(owner_conn):
    """关键业务唯一约束必须存在（漏了就会产生重复行，而不是报错）。"""
    problems = []
    for table, expected in KEY_UNIQUE_CONSTRAINTS.items():
        actual = _unique_key_sets(owner_conn, table)
        for cols in sorted(expected, key=sorted):
            if cols not in actual:
                problems.append(f'{table}{sorted(cols)}')
    assert not problems, '缺少唯一约束：' + '; '.join(problems)


def test_insertion_order_columns_are_identity(owner_conn):
    """插入顺序列 seq：必须是 BY DEFAULT IDENTITY，且有 (project_id,..,seq) 索引。

    BY DEFAULT（而非 GENERATED ALWAYS）是刻意的：`Repository.import_projection`
    会按「导出载荷里出现、且表里存在」的列动态拼 INSERT，显式给值时必须被接受。
    """
    with owner_conn.cursor() as cur:
        cur.execute("""select table_name, is_identity, identity_generation
                         from information_schema.columns
                        where table_schema = 'public' and column_name = 'seq'""")
        identities = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

    indexes = _index_key_sets(owner_conn)
    problems = []
    for table in INSERTION_ORDER_TABLES:
        found = identities.get(table)
        if found != ('YES', 'BY DEFAULT'):
            problems.append(f'{table}.seq 不是 BY DEFAULT IDENTITY（实际 {found}）')
            continue
        if not any(t == table and 'project_id' in cols and 'seq' in cols
                   for t, cols in indexes):
            problems.append(f'{table} 缺 (project_id,..,seq) 索引')
    assert not problems, '插入顺序列不合格：' + '; '.join(problems)


# ── 触发器行为验收 ──────────────────────────────────────────────────────────
# 每个用例都用独立的临时项目，测完随事务回滚，不留数据。
# 「维护开关」= 会话级 GUC knowledge.ontology_history_delete_allowed。

_PROJECT = 'proj-schema-test'
_DRAFT = 'draft-schema-test'


def _purge_project(conn, project_id):
    """按依赖顺序清理一个项目。

    顺序不可颠倒：决定引用操作、操作引用草案、草案引用项目。
    不可变触发器要求打开维护开关（会话级 GUC）。
    **不清理 ontology_history_repairs** —— 该台账追加写且 DELETE 被无条件拒绝。
    """
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("set local knowledge.ontology_history_delete_allowed = '1'")
            for table in ('ontology_review_decisions', 'ontology_operations',
                          'ontology_publish_requests'):
                cur.execute(f'delete from {table} where project_id = %s', (project_id,))
    with conn.cursor() as cur:
        cur.execute('delete from projects where id = %s', (project_id,))


@pytest.fixture()
def seeded(owner_conn):
    """建一个临时项目 + 草案，作为操作/决定的宿主；前后都清理，可重复运行。"""
    _purge_project(owner_conn, _PROJECT)
    with owner_conn.cursor() as cur:
        cur.execute("insert into projects(id,name,metadata,created_at) values (%s,'t','{}',now())",
                    (_PROJECT,))
        cur.execute("""insert into ontology_drafts(id,project_id,source_kind,status,revision,
                       title,summary,source_context,created_at,updated_at)
                       values (%s,%s,'manual','editing',1,'t','s','{}',now(),now())""",
                    (_DRAFT, _PROJECT))
    yield
    _purge_project(owner_conn, _PROJECT)


def _insert_operation(cur, op_id, supersedes=None, fingerprint='fp1'):
    cur.execute("""insert into ontology_operations(id,project_id,draft_id,action,target_iri,
                   before_json,after_json,evidence_json,impact_json,validation_json,risk,
                   fingerprint,supersedes_operation_id,created_at)
                   values (%s,%s,%s,'add','iri:x','{}','{}','{}','{}','{}','low',%s,%s,now())""",
                (op_id, _PROJECT, _DRAFT, fingerprint, supersedes))


def _insert_decision(cur, dec_id, operation_id, fingerprint='fp1', supersedes=None):
    cur.execute("""insert into ontology_review_decisions(id,project_id,draft_id,operation_id,
                   operation_fingerprint,action,actor,supersedes_decision_id,created_at)
                   values (%s,%s,%s,%s,%s,'approve','tester',%s,now())""",
                (dec_id, _PROJECT, _DRAFT, operation_id, fingerprint, supersedes))


def _expect_rejected(conn, statement, params=None):
    """语句必须被拒绝（触发器/FK/约束触发），且拒绝后连接仍可用。"""
    with pytest.raises(psycopg.Error):
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(statement, params)
    with conn.cursor() as cur:
        cur.execute('select 1')
        assert cur.fetchone()[0] == 1, '被拒绝后连接应仍可用'


def test_repair_ledger_accepts_new_key(owner_conn):
    """台账新键必须能插入 —— 无条件拒绝会导致台账永远写不进去（曾出现的 bug）。

    台账是追加写且不可删除的，故用唯一键保证可重复运行，不试图清理记录。
    """
    key = f'rk-schema-{uuid4().hex[:12]}'
    with owner_conn.cursor() as cur:
        cur.execute("""insert into ontology_history_repairs(repair_key,schema_version,reason,
                       operations_repaired,decisions_repaired,repaired_at)
                       values (%s,1,'test',0,0,now())""", (key,))
        cur.execute('select reason from ontology_history_repairs where repair_key = %s', (key,))
        assert cur.fetchone()[0] == 'test'


def test_repair_ledger_rejects_duplicate_key(owner_conn):
    """台账：重复键、UPDATE、DELETE 一律拒绝（追加写 + 不可变）。"""
    key = f'rk-schema-{uuid4().hex[:12]}'
    with owner_conn.cursor() as cur:
        cur.execute("""insert into ontology_history_repairs(repair_key,schema_version,reason,
                       operations_repaired,decisions_repaired,repaired_at)
                       values (%s,1,'test',0,0,now())""", (key,))
    _expect_rejected(owner_conn, """insert into ontology_history_repairs(repair_key,schema_version,
        reason,operations_repaired,decisions_repaired,repaired_at)
        values (%s,1,'dup',0,0,now())""", (key,))
    _expect_rejected(owner_conn, "update ontology_history_repairs set reason='x' "
                                 "where repair_key = %s", (key,))
    _expect_rejected(owner_conn, "delete from ontology_history_repairs where repair_key = %s",
                     (key,))


def test_operation_immutability(owner_conn, seeded):
    """操作：可插入、可成链；重复 id / 自环 / UPDATE / 未授权 DELETE 全部拒绝。"""
    with owner_conn.cursor() as cur:
        _insert_operation(cur, 'op-schema-1')
        _insert_operation(cur, 'op-schema-2', supersedes='op-schema-1')

    _expect_rejected(owner_conn, """insert into ontology_operations(id,project_id,draft_id,action,
        target_iri,before_json,after_json,evidence_json,impact_json,validation_json,risk,
        fingerprint,created_at) values ('op-schema-1',%s,%s,'add','i','{}','{}','{}','{}','{}',
        'low','fp1',now())""", (_PROJECT, _DRAFT))
    _expect_rejected(owner_conn, """insert into ontology_operations(id,project_id,draft_id,action,
        target_iri,before_json,after_json,evidence_json,impact_json,validation_json,risk,
        fingerprint,supersedes_operation_id,created_at) values ('op-schema-3',%s,%s,'add','i',
        '{}','{}','{}','{}','{}','low','fp1','op-schema-3',now())""", (_PROJECT, _DRAFT))
    _expect_rejected(owner_conn, "update ontology_operations set risk='high' where id='op-schema-1'")
    _expect_rejected(owner_conn, "delete from ontology_operations where id='op-schema-2'")

    # 维护开关打开后才允许删除
    with owner_conn.transaction():
        with owner_conn.cursor() as cur:
            cur.execute("set local knowledge.ontology_history_delete_allowed = '1'")
            cur.execute("delete from ontology_operations where id='op-schema-2'")
    with owner_conn.cursor() as cur:
        cur.execute("select count(*) from ontology_operations where id='op-schema-2'")
        assert cur.fetchone()[0] == 0


def test_decision_fingerprint_binding_and_immutability(owner_conn, seeded):
    """决定：必须绑定真实操作指纹；重复 id / 自环 / UPDATE 全部拒绝。"""
    with owner_conn.cursor() as cur:
        _insert_operation(cur, 'op-dec-1', fingerprint='fp-dec')
        _insert_decision(cur, 'dec-schema-1', 'op-dec-1', fingerprint='fp-dec')

    _expect_rejected(owner_conn, """insert into ontology_review_decisions(id,project_id,draft_id,
        operation_id,operation_fingerprint,action,actor,created_at)
        values ('dec-schema-2',%s,%s,'op-dec-1','WRONG','approve','t',now())""",
        (_PROJECT, _DRAFT))
    _expect_rejected(owner_conn, """insert into ontology_review_decisions(id,project_id,draft_id,
        operation_id,operation_fingerprint,action,actor,created_at)
        values ('dec-schema-1',%s,%s,'op-dec-1','fp-dec','approve','t',now())""",
        (_PROJECT, _DRAFT))
    _expect_rejected(owner_conn, """insert into ontology_review_decisions(id,project_id,draft_id,
        operation_id,operation_fingerprint,action,actor,supersedes_decision_id,created_at)
        values ('dec-schema-3',%s,%s,'op-dec-1','fp-dec','approve','t','dec-schema-3',now())""",
        (_PROJECT, _DRAFT))
    _expect_rejected(owner_conn, "update ontology_review_decisions set actor='x' "
                                 "where id='dec-schema-1'")


@pytest.mark.skipif(not APP_DSN, reason='未设置 KG_TEST_POSTGRES_APP_DSN')
def test_app_role_can_dml_but_not_ddl():
    """应用角色：可读写数据，但无 schema DDL 权限（规划 §5.1 的最小权限）。"""
    conn = psycopg.connect(APP_DSN, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute('select count(*) from projects')
            cur.fetchone()

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.cursor() as cur:
                cur.execute('create table schema_should_not_exist(x int)')

        # DML 必须可用（用回滚验证，不留数据）
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("insert into projects(id,name,metadata,created_at) "
                            "values ('p-app-role-test','x','{}',now())")
                raise RuntimeError('rollback')
    except RuntimeError:
        pass
    finally:
        conn.close()
