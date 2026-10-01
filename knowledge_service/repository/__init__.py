"""存储层：基于 PostgreSQL 的事务性真值存储（一次修订修正一整条稳定记录）。

拆分为多个职责域文件，避免单文件上帝对象：
- core.py            主存储：项目/记录/本体/制品/检索索引/查询
- assertions_store   断言（审核候选）生命周期
- ingest_store       摄取运行与阶段输出
- review_store       消歧审核与合并账本
- pg_engine.py       PostgreSQL 连接与方言适配层

Repository 保持方法名不变、内部转发到各 Store，对外导入路径零破坏。

schema 由编号迁移管理（`migrations/` + `migrate.py`），Repository 不再自建表。
"""
from .core import (
    Repository,
    OntologyNotPublished,
    OntologyPublicationConflict,
    vector_blob,
    vector_array,
    _json,
)
from .ontology_draft_store import OntologyDraftConflict, OntologyDraftStore
from .discovery_run_store import DiscoveryRunConflict, DiscoveryRunStore

__all__ = [
    'Repository', 'OntologyNotPublished', 'OntologyPublicationConflict',
    'vector_blob', 'vector_array', '_json',
    'OntologyDraftConflict', 'OntologyDraftStore',
    'DiscoveryRunConflict', 'DiscoveryRunStore',
]
