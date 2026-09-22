"""版本化知识、本体与检索服务。

FastAPI 标准分层：api（路由）→ services（业务）→ repository（存储）
→ core/utils（基础设施）→ integrations（外部集成）。

统一出口：外部使用 ``from knowledge_service import create_app, Repository`` 等，
不必关心内部模块路径。
"""
from .api import create_app
from .repository import OntologyNotPublished, Repository
from .services.service import KnowledgeService

__version__ = "1.1.0"

__all__ = ['create_app', 'Repository', 'OntologyNotPublished', 'KnowledgeService', '__version__']
