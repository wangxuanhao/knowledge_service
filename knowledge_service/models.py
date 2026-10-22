from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .utils.attributes import primitive_datatype
from .core.time import normalize_time
from .domain.applicability import normalize_applicability


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


class ApplicabilityDimension(Request):
    mode: Literal['specific', 'all', 'unknown'] = 'unknown'
    values: list[str] = Field(default_factory=list)


class Applicability(Request):
    platforms: ApplicabilityDimension = Field(default_factory=ApplicabilityDimension)
    businesses: ApplicabilityDimension = Field(default_factory=ApplicabilityDimension)
    audiences: ApplicabilityDimension = Field(default_factory=ApplicabilityDimension)

    @model_validator(mode='before')
    @classmethod
    def normalize_scope(cls, value):
        return normalize_applicability(value)


class Scope(Request):
    applicability: Applicability | None = None
    filters: dict[str, Any] | None = None
    valid_at: str | None = None
    known_at: str | None = None
    include_unknown: bool = True
    kinds: list[Literal['document', 'entity', 'relation', 'attribute', 'chunk']] | None = None
    ontology_scope: Literal['all', 'ids', 'unknown'] = 'all'
    ontology_ids: list[str] | None = None

    @model_validator(mode='after')
    def check_ontology_scope(self):
        if self.ontology_scope == 'ids':
            normalized = [ontology_id.strip() for ontology_id in self.ontology_ids or []]
            if not normalized or any(not ontology_id for ontology_id in normalized):
                raise ValueError('ids 本体范围需要非空 ontology_ids')
            self.ontology_ids = list(dict.fromkeys(normalized))
        elif self.ontology_ids is not None:
            raise ValueError('只有 ids 本体范围可以提供 ontology_ids')
        return self


class Search(Scope):
    query: str = Field(min_length=1, max_length=10000)
    k: int = Field(default=10, ge=1, le=100)
    retrieval_mode: Literal['hybrid', 'semantic', 'keyword'] = 'hybrid'
    k_entities: int = Field(default=5, ge=0, le=50)
    k_chunks: int = Field(default=5, ge=0, le=50)
    k_relations: int = Field(default=5, ge=0, le=50)
    k_per_platform: int = Field(default=5, ge=1, le=20)
    k_unknown: int = Field(default=3, ge=1, le=20)
    # 本体扩展（P0-1）：开启后顺着本体的父子关系补召回 —— 查询里出现类名（含业务标签）时，
    # 命中该类的**全部子类**；这些子类实例即使正文里没有查询词也会被召回，关键词也会按类名/别名
    # 多词 OR 扩召回（同一条记录取最高分，不累加）。
    # 只补召回、不放宽可见范围：权限与时态仍由 scope 唯一裁决。
    # 默认关：关闭时检索路径与响应字段和改动前逐字节一致。
    ontology_expansion: bool = Field(default=False, description=(
        '本体扩展：按本体父子关系补召回（查父类名也会命中子类实例）。'
        '只补召回，不放宽可见范围；关闭时行为与不传该字段一致。'))


class Question(Search):
    include_references: bool = True
    reference_k: int = Field(default=5, ge=1, le=20)
    generate: bool = False
    hops:int=Field(default=2,ge=0,le=3)


class ResolutionReviewDecision(Request):
    decision: Literal['merged','separate','rejected']
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1,max_length=2000)
    expected_entity_versions: dict[str,int] = Field(default_factory=dict)
    attribute_winners: list[str] = Field(default_factory=list)
    preview_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)

class IngestSettings(Request):
    metadata: dict[str, Any] = Field(default_factory=dict)
    applicability: Applicability | None = None
    published_at: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    extraction_mode: Literal['ontology','discovery','documents'] = 'ontology'
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
        self.metadata = dict(self.metadata)
        scope = normalize_applicability(self.applicability if self.applicability is not None
                                        else self.metadata.get('applicability'))
        self.metadata['applicability'] = scope
        published = self.published_at if self.published_at is not None else self.metadata.get('published_at')
        self.published_at = normalize_time(published)
        if self.published_at is not None:
            self.metadata['published_at'] = self.published_at
        explicit = self.valid_from is not None
        self.valid_from = normalize_time(self.valid_from) if explicit else self.published_at
        self.valid_until = normalize_time(self.valid_until)
        if self.valid_from and self.valid_until and self.valid_until <= self.valid_from:
            raise ValueError('失效时间必须晚于生效时间')
        self.metadata['effective_time_source'] = ('explicit' if explicit else
            'published_at' if self.published_at else 'unknown')
        return self

class Ingest(IngestSettings):
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1, max_length=1_000_000)


class UploadOptions(IngestSettings):
    title: str | None = Field(default=None, max_length=300)


class Sparql(Scope):
    query: str = Field(min_length=1, max_length=10000)
    ontology_id: str | None = None

class IngestCapabilities(BaseModel):
    max_batch_items: int
    max_file_bytes: int
    max_batch_bytes: int
    concurrency_choices: list[str | int]
    server_concurrency_limit: int
    llm_concurrency_limit: int
    ocr_concurrency_limit: int
    ocr_available: bool
    ocr_languages: list[str]
    ocr_image_extensions: list[str]
    ocr_max_pixels: int
    supported_extensions: list[str]
    markdown_package: bool
    source_reader: bool

class IngestBatchSettings(IngestSettings):
    requested_concurrency: Literal['auto',1,2,4,8] = 'auto'

class IngestBatchCreate(Request):
    settings: IngestBatchSettings = Field(default_factory=IngestBatchSettings)

class IngestItemRetry(Request):
    mode: Literal['resume_checkpoint','retry_failed_chunks','restart_document'] = 'resume_checkpoint'
