from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .utils.attributes import primitive_datatype


class Request(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ProjectCreate(Request):
    name: str = Field(min_length=1, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)
    use_default_ontology: bool = True
    ontology_mode: Literal['ontology','discovery','documents'] | None = None


class ProjectUpdate(Request):
    name: str = Field(min_length=1, max_length=200)


class RecordWrite(Request):
    id: str | None = Field(default=None, min_length=1, max_length=200)
    kind: Literal['document', 'entity', 'relation', 'attribute', 'chunk']
    text: str = Field(min_length=1, max_length=1_000_000)
    type: str = ''
    metadata: dict[str, Any] = Field(default_factory=dict)
    properties: dict[str, Any] = Field(default_factory=dict)
    source_id: str | None = None
    subject_id: str | None = None
    object_id: str | None = None
    value: str | bool | int | float | None = None
    datatype: str | None = None
    ontology_id: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None

    @field_validator('value', mode='before')
    @classmethod
    def check_native_attribute_value(cls, value):
        if value is not None and type(value) not in (str, bool, int, float):
            raise ValueError('属性值必须使用原生字符串、布尔值、整数或浮点数')
        return value

    @model_validator(mode='after')
    def check_attribute(self):
        if self.kind != 'attribute':
            return self
        if not self.subject_id or not self.type or self.value is None or not self.datatype:
            raise ValueError('属性记录需要 subject_id、type、value 和 datatype')
        if self.datatype != primitive_datatype(self.value):
            raise ValueError('属性 datatype 必须与值的原始类型一致')
        return self


class Revision(Request):
    record: RecordWrite
    expected_version: int = Field(ge=1)


class RecordBatch(Request):
    records: list[RecordWrite] = Field(min_length=1, max_length=1000)


class Scope(Request):
    filters: dict[str, Any] | None = None
    valid_at: str | None = None
    known_at: str | None = None
    include_unknown: bool = True
    kinds: list[Literal['document', 'entity', 'relation', 'attribute', 'chunk']] | None = None


class Search(Scope):
    query: str = Field(min_length=1, max_length=10000)
    k: int = Field(default=10, ge=1, le=100)
    retrieval_mode: Literal['hybrid', 'semantic', 'keyword'] = 'hybrid'
    k_entities: int = Field(default=5, ge=0, le=50)
    k_chunks: int = Field(default=5, ge=0, le=50)
    k_relations: int = Field(default=5, ge=0, le=50)


class Question(Search):
    generate: bool = False
    hops:int=Field(default=2,ge=0,le=3)


class ResolutionReviewDecision(Request):
    decision: Literal['merged','separate','rejected']
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1,max_length=2000)
    actor: str = Field(default='reviewer',min_length=1,max_length=200)
    expected_entity_versions: dict[str,int] = Field(default_factory=dict)


class IngestSettings(Request):
    metadata: dict[str, Any] = Field(default_factory=dict)
    valid_from: str | None = None
    valid_until: str | None = None
    extract: bool = True
    extraction_mode: Literal['ontology','discovery','documents'] | None = None
    extract_attributes: bool = False
    relation_constraint_mode: Literal['advisory','review','strict','off'] = 'review'
    ontology_id: str | None = None
    resolve_entities:bool=True
    auto_merge:bool=False
    review_threshold:float=Field(default=.72,ge=0,le=1)
    merge_threshold:float=Field(default=.88,ge=0,le=1)
    chunk_strategy: Literal['fixed', 'paragraph', 'structural'] = 'fixed'
    chunk_size: int = Field(default=1800, ge=100, le=10000)
    chunk_overlap: int = Field(default=200, ge=0, le=2000)

    @model_validator(mode='after')
    def check_chunk_settings(self):
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError('重叠长度必须小于切片长度')
        if self.auto_merge and not self.resolve_entities:
            raise ValueError('语义合并需要先开启实体消歧')
        if self.review_threshold >= self.merge_threshold:
            raise ValueError('审核阈值必须低于自动合并阈值')
        return self

    def effective_extraction_mode(self):
        return self.extraction_mode or ('ontology' if self.extract else 'documents')


class Ingest(IngestSettings):
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1, max_length=1_000_000)


class UploadOptions(IngestSettings):
    title: str | None = Field(default=None, max_length=300)


class OntologyWrite(Request):
    turtle: str = Field(min_length=1, max_length=1_000_000)


class Sparql(Scope):
    query: str = Field(min_length=1, max_length=10000)
    ontology_id: str | None = None
