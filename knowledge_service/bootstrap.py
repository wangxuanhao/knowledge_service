"""Application composition dependencies.

Only this module knows the concrete persistence and integration adapters.  The
HTTP package imports these composition-time values from here and otherwise
talks to application services.
"""
from .integrations.document_parser import DocumentParseError
from .integrations.embeddings import (
    EmbeddingDimensionConfigurationError,
    configured_encoder,
    encoder_dimension,
)
from .integrations.milvus_store import MilvusSchemaMismatch, MilvusStore, VectorDimensionMismatch
from .integrations.neo4j_store import Neo4jProjectionAdapter, install as install_neo4j
from .repository import (
    OntologyNotPublished,
    OntologyPublicationConflict,
    Repository,
)
from .repository.connection import resolve_dsn
from .repository.users_store import UserStore

__all__ = [
    'DocumentParseError', 'EmbeddingDimensionConfigurationError',
    'configured_encoder', 'encoder_dimension', 'MilvusStore', 'MilvusSchemaMismatch',
    'VectorDimensionMismatch', 'Neo4jProjectionAdapter', 'install_neo4j', 'OntologyNotPublished',
    'OntologyPublicationConflict', 'Repository', 'resolve_dsn', 'UserStore',
]
