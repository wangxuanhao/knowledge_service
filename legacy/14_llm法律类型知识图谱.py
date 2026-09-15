"""
Semantica 法律法规生产级完整服务
================================
完整 8 阶段管线：
  Ingest → Parse → Normalize → Split → Extract →
  Conflict → Dedup → KG Build →
  Ontology / Reasoning / Provenance / Context →
  Vector Store + Graph Store + Export

- LLM 抽取：Qwen（通过 LiteLLM OpenAI 兼容模式）
- 长文本：TextSplitter 实体感知 + 递归 fallback
- 自定义实体类型：可动态注册
- 动态本体：可随时扩展类与属性
- 法律法规场景示例内置

运行前：
  pip install semantica[llm-all] openai
  export DASHSCOPE_API_KEY="sk-xxx"
"""

import os
import re
import time
import json
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from datetime import datetime

# 国内环境：跳过 fastembed 在线下载（如需向量化再开启）
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("SEMANTICA_ALLOW_ANONYMOUS", "true")


# ============================================================
# 数据类：自定义实体 / 关系 / 本体
# ============================================================

@dataclass
class CustomEntity:
    """自定义实体（兼容 Semantica Entity 接口）"""
    text: str
    label: str
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def start_char(self):
        return self.metadata.get("start_char", 0)

    @property
    def end_char(self):
        return self.metadata.get("end_char", len(self.text))


@dataclass
class CustomRelation:
    """自定义关系（兼容 Semantica Relation 接口）"""
    subject: CustomEntity
    predicate: str
    object: CustomEntity
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)


# ============================================================
# 完整服务类
# ============================================================

class LawGraphService:
    """
    法律法规知识图谱生产服务
    
    使用方式：
    svc = LawGraphService(qwen_model="qwen-plus")
    svc.add_entity_type("CASE", "案件")               # 注册自定义实体类型
    svc.add_entity("（2023）京01民初123号", "CASE")   # 动态添加实体
    svc.add_ontology_class("CivilCase", "民事案件", parent="Case")
    result = svc.process("判决书.txt")                 # 单文件
    result = svc.process_directory("cases/")           # 批量
    """

    def __init__(
        self,
        qwen_model: str = "qwen-plus",
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        enable_vector: bool = False,       # 向量化默认关（需外部配置）
        graph_backend: Optional[str] = None,  # 可选 "neo4j" 等
        # ---- 外部向量化配置（都不传就跳过向量化）----
        embedding_provider: Optional[str] = None,   # 例如 "openai" / "fastembed" / "huggingface"
        embedding_model: Optional[str] = None,       # 例如 "BAAI/bge-small-zh-v1.5" / HF 模型名 / OpenAI 模型名
        embedding_base_url: Optional[str] = None,   # OpenAI 兼容 endpoint（外部向量化时用）
        embedding_api_key: Optional[str] = None,    # 外部向量化服务的 key（不传读 EMBEDDING_API_KEY）
    ):
        self.qwen_model = qwen_model
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.enable_vector = enable_vector
        self.graph_backend = graph_backend

        # 外部向量化配置（全部由调用方控制，源码不写死任何路径/provider）
        self.embedding_provider = embedding_provider
        self.embedding_model = embedding_model
        self.embedding_base_url = embedding_base_url
        self.embedding_api_key = embedding_api_key or os.getenv("EMBEDDING_API_KEY", "")

        # DashScope key 只从环境变量读，源码不接 kwargs，避免误把 key 写进代码
        self.dashscope_api_key = os.getenv("DASHSCOPE_API_KEY", "")
        if not self.dashscope_api_key:
            print("⚠️  DASHSCOPE_API_KEY 未设置（请用环境变量注入），LLM 调用将失败")

        # ---------- 1. LLM Provider: Qwen via LiteLLM ----------
        self.llm = self._init_qwen()

        # ---------- 2. 初始化 Semantica 各模块 ----------
        from semantica.ingest import FileIngestor
        from semantica.parse import DocumentParser, DoclingParser
        from semantica.normalize import TextNormalizer
        from semantica.split import TextSplitter
        from semantica.semantic_extract import NERExtractor, RelationExtractor, CoreferenceResolver
        from semantica.conflicts import ConflictDetector
        from semantica.deduplication import DuplicateDetector, EntityMerger
        from semantica.kg import GraphBuilder
        from semantica.ontology import OntologyGenerator
        from semantica.provenance import ProvenanceManager
        from semantica.context import ContextGraph
        from semantica.export import LPGExporter

        self.ingestor = FileIngestor()
        self.parser = DocumentParser()          # 普通文档
        self.docling_parser = DoclingParser()   # 复杂 PDF（带表格/多栏）
        self.normalizer = TextNormalizer()
        # 长文本拆分：实体感知优先，失败 fallback 到递归
        self.splitter = TextSplitter(
            method=["entity_aware", "recursive"],
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            ner_method="llm",
            llm_provider=self.llm,
        )
        self.ner = NERExtractor(method="llm", llm_provider=self.llm, confidence_threshold=0.6)
        self.rel = RelationExtractor(method="llm", llm_provider=self.llm, confidence_threshold=0.6)
        self.coref = CoreferenceResolver(method="rule")
        self.conflict_detector = ConflictDetector()
        self.dup_detector = DuplicateDetector()
        self.entity_merger = EntityMerger()
        self.graph_builder = GraphBuilder(merge_entities=True)
        self.onto_gen = OntologyGenerator()
        self.prov_mgr = ProvenanceManager()
        self.ctx_graph = ContextGraph()
        self.lpg_exporter = LPGExporter()
        # ---------- 推理引擎（Reasoner facade，规则用 IF-THEN 字符串）----------
        # 实证：semantica.reasoning.reasoner 的 ReteEngine 期望 condition 是字符串模板
        # （_extract_bindings 内部 isinstance(condition, str) 判定，dict 形式被丢弃）。
        # 13 用 dict 写条件，匹配不到任何东西。这里用高层 Reasoner，
        # 它接受 "IF Predicate(?x, ?y) THEN Predicate(?y, ?z)" 字符串。
        from semantica.reasoning import Reasoner
        self.reasoner = Reasoner()
        # 法律场景规则（与文档事实无关的纯本体/法律关系推演）
        # 注意：__init__ 只挂一个空 Reasoner，规则不在这注册——
        # 避免 process() 内部 infer_with_results(rules=...) 触发 add_rule 幂等检查
        # 在日志里刷 "Skipping duplicate rule" warning。规则在 process() 阶段单点注册。
        self.law_rules: List[str] = [
            # 1) 法条继承母法生效日：law cites article + law enactedOn date ⇒ article effectiveOn date
            "IF cites(?law, ?article) AND enactedOn(?law, ?date) "
            "THEN effectiveOn(?article, ?date)",
            # 2) 案件当事人代理转承：case partyTo party + party representedBy agent ⇒ case partyTo agent
            "IF partyTo(?case, ?party) AND representedBy(?party, ?agent) "
            "THEN partyTo(?case, ?agent)",
            # 3) 违法→处罚暴露：party violates article + article penalizes penalty ⇒ party exposesTo penalty
            "IF violates(?party, ?article) AND penalizes(?article, ?penalty) "
            "THEN exposesTo(?party, ?penalty)",
        ]

        # ---------- 3. 预留：自定义实体类型 / 实体 / 关系 / 本体 ----------
        self.custom_entity_types: Dict[str, str] = {}   # type_id -> 中文描述
        self.custom_entities: List[CustomEntity] = []
        self.custom_relations: List[CustomRelation] = []
        self.custom_ontology_classes: List[Dict] = []
        self.custom_ontology_props: List[Dict] = []

        # 加载法律法规预设
        self._preset_law_entity_types()
        self._preset_law_entities()
        self._preset_law_ontology()

    # ------------------------------------------------------------
    # 内部：Qwen 初始化（LiteLLM OpenAI 兼容模式）
    # ------------------------------------------------------------
    def _init_qwen(self):
        """
        预留：调用 Qwen 模型的方法
        
        通过 LiteLLM 接入 DashScope 的 OpenAI 兼容接口
        模型可选：qwen-turbo / qwen-plus / qwen-max / qwen-long
        """
        from semantica.llms import LiteLLM

        return LiteLLM(
            model=f"openai/{self.qwen_model}",   # 用 openai/ 前缀 + DashScope 兼容 endpoint 走 LiteLLM
            api_key=self.dashscope_api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            temperature=0.1,
            max_tokens=4096,
        )

    # ------------------------------------------------------------
    # 内部：法律法规预设
    # ------------------------------------------------------------
    def _preset_law_entity_types(self):
        """预留案例：法律法规实体类型体系"""
        presets = {
            "LAW": "法律法规",
            "ARTICLE": "法条",
            "LEGAL_CONCEPT": "法律概念",
            "COURT": "司法机关",
            "CASE": "案件",
            "PARTY": "当事人",
            "CONTRACT": "合同",
            "CLAIM": "诉讼请求",
            "EVIDENCE": "证据",
            "JUDGMENT": "判决结果",
            "OBLIGATION": "义务",
            "RIGHT": "权利",
            "PENALTY": "处罚",
            "PRECEDENT": "判例",
        }
        self.custom_entity_types.update(presets)
        print(f"  ✓ 已注册 {len(presets)} 个法律实体类型")

    def _preset_law_entities(self):
        """预留案例：法律法规核心实体（额外字段全部走 metadata）"""
        examples = [
            CustomEntity("《中华人民共和国民法典》", "LAW",
                         metadata={"effective_date": "2021-01-01", "level": "基本法律"}),
            CustomEntity("《中华人民共和国公司法》", "LAW",
                         metadata={"effective_date": "2024-07-01", "level": "基本法律"}),
            CustomEntity("《中华人民共和国个人信息保护法》", "LAW",
                         metadata={"effective_date": "2021-11-01", "level": "基本法律"}),
            CustomEntity("《数据安全法》", "LAW",
                         metadata={"effective_date": "2021-09-01", "level": "基本法律"}),
            CustomEntity("违约责任", "LEGAL_CONCEPT", metadata={"category": "合同编"}),
            CustomEntity("不可抗力", "LEGAL_CONCEPT", metadata={"category": "合同编"}),
            CustomEntity("数据合规", "LEGAL_CONCEPT", metadata={"category": "新兴领域"}),
            CustomEntity("个人信息权益", "RIGHT", metadata={"category": "人格权"}),
            CustomEntity("最高人民法院", "COURT", metadata={"level": "最高"}),
            CustomEntity("北京市第一中级人民法院", "COURT", metadata={"level": "中级"}),
            CustomEntity("违约金", "OBLIGATION", metadata={"category": "救济"}),
        ]
        self.custom_entities.extend(examples)
        print(f"  ✓ 已加载 {len(examples)} 个法律示例实体")

    def _preset_law_ontology(self):
        """预留案例：法律法规本体层级"""
        self.custom_ontology_classes.extend([
            # 法律层级
            {"id": "Law", "label": "法律", "parent": None,
             "description": "法律法规顶层类"},
            {"id": "CivilCode", "label": "民法典", "parent": "Law"},
            {"id": "CompanyLaw", "label": "公司法", "parent": "Law"},
            {"id": "DataProtectionLaw", "label": "数据保护法", "parent": "Law"},
            {"id": "ContractLaw", "label": "合同法", "parent": "Law"},
            # 法律概念
            {"id": "LegalConcept", "label": "法律概念", "parent": None},
            {"id": "BreachOfContract", "label": "违约责任", "parent": "LegalConcept"},
            {"id": "ForceMajeure", "label": "不可抗力", "parent": "LegalConcept"},
            {"id": "DataCompliance", "label": "数据合规", "parent": "LegalConcept"},
            # 案件
            {"id": "Case", "label": "案件", "parent": None},
            {"id": "CivilCase", "label": "民事案件", "parent": "Case"},
            {"id": "CriminalCase", "label": "刑事案件", "parent": "Case"},
            {"id": "AdminCase", "label": "行政案件", "parent": "Case"},
        ])
        self.custom_ontology_props.extend([
            {"property": "hasArticle", "domain": "Law", "range": "Article",
             "description": "法律包含的法条"},
            {"property": "hasEffectiveDate", "domain": "Law", "range": "Date"},
            {"property": "citedAs", "domain": "Judgment", "range": "Article",
             "description": "判决引用的法条"},
            {"property": "filedIn", "domain": "Case", "range": "Court"},
            {"property": "penaltyAmount", "domain": "Penalty", "range": "Money"},
            {"property": "requiresConsent", "domain": "DataProcessing", "range": "Boolean"},
        ])
        print(f"  ✓ 已加载 {len(self.custom_ontology_classes)} 个法律本体类, "
              f"{len(self.custom_ontology_props)} 个属性")

    # ------------------------------------------------------------
    # 对外接口：动态扩展
    # ------------------------------------------------------------
    def add_entity_type(self, type_id: str, description: str):
        """动态注册自定义实体类型"""
        self.custom_entity_types[type_id] = description
        print(f"  + 实体类型: {type_id} ({description})")

    def add_entity(self, text: str, label: str, confidence: float = 1.0, **metadata):
        """动态添加自定义实体"""
        ent = CustomEntity(text=text, label=label, confidence=confidence, metadata=metadata)
        self.custom_entities.append(ent)
        print(f"  + 实体: {text}  [{label}]")
        return ent

    def add_relation(self, subj: CustomEntity, predicate: str, obj: CustomEntity,
                     confidence: float = 1.0):
        """动态添加自定义关系"""
        rel = CustomRelation(subject=subj, predicate=predicate, object=obj,
                             confidence=confidence)
        self.custom_relations.append(rel)
        print(f"  + 关系: ({subj.text}) --[{predicate}]--> ({obj.text})")
        return rel

    def add_ontology_class(self, class_id: str, label: str, parent: str = None, **kwargs):
        """动态添加本体类"""
        cls = {"id": class_id, "label": label, "parent": parent, **kwargs}
        self.custom_ontology_classes.append(cls)
        print(f"  + 本体类: {label}  (父类: {parent})")

    def add_ontology_property(self, property_name: str, domain: str, range_type: str, **kwargs):
        """动态添加本体属性"""
        prop = {"property": property_name, "domain": domain, "range": range_type, **kwargs}
        self.custom_ontology_props.append(prop)
        print(f"  + 本体属性: {property_name}  ({domain} → {range_type})")

    # ------------------------------------------------------------
    # 核心管线：处理单个文档
    # ------------------------------------------------------------
    def process(self, input_source: str) -> Dict[str, Any]:
        """
        处理单个文件或文本，返回完整结果
        
        Args:
            input_source: 文件路径 或 原始文本
        Returns:
            {
                "kg": 知识图谱,
                "ontology": 本体,
                "entities": 合并后的实体列表,
                "relations": 关系列表,
                "chunks": 分块信息,
                "conflicts": 冲突,
                "stats": 统计信息,
            }
        """
        t_start = time.time()
        result: Dict[str, Any] = {}
        stats: Dict[str, Any] = {}

        # ===== 1. INGEST =====
        print(f"\n{'='*60}\n  📂 INGEST\n{'='*60}")
        if Path(input_source).exists():
            src = self.ingestor.ingest_file(input_source)
            stats["source"] = src.name
            stats["size_bytes"] = src.size
        else:
            src = type('Obj', (), {'path': None, 'name': 'inline_text',
                                   'size': len(input_source)})()
            stats["source"] = "inline_text"

        # ===== 2. PARSE =====
        print(f"\n{'='*60}\n  🔍 PARSE\n{'='*60}")
        if getattr(src, 'path', None):
            # 复杂 PDF 用 Docling，普通格式用 DocumentParser
            if src.path.lower().endswith('.pdf'):
                parsed = self.docling_parser.parse(src.path)
            else:
                parsed = self.parser.parse_document(src.path)
            text = parsed.get("text", parsed.get("full_text", ""))
        else:
            text = input_source
        stats["parsed_chars"] = len(text)
        print(f"  ✓ 解析文本: {len(text)} 字符")

        # ===== 3. NORMALIZE =====
        print(f"\n{'='*60}\n  🧹 NORMALIZE\n{'='*60}")
        t0 = time.time()
        norm_text = self.normalizer.normalize_text(text)
        stats["normalize_ms"] = round((time.time() - t0) * 1000, 1)
        print(f"  ✓ 归一化完成: {len(norm_text)} 字符")

        # ===== 4. SPLIT（长文本核心） =====
        print(f"\n{'='*60}\n  ✂️  SPLIT（长文本分块）\n{'='*60}")
        t0 = time.time()
        chunks = self.splitter.split(norm_text)
        stats["split_ms"] = round((time.time() - t0) * 1000, 1)
        stats["num_chunks"] = len(chunks)
        print(f"  ✓ 分块策略: {self.splitter.methods}")
        print(f"  ✓ 分块数: {len(chunks)}  "
              f"(每块 ~{self.chunk_size} 字符, 重叠 {self.chunk_overlap})")
        for i, ch in enumerate(chunks[:3]):
            print(f"    块{i}: [{ch.start_index}:{ch.end_index}] "
                  f"{len(ch.text)} 字符 实体数: {ch.metadata.get('entity_count', '?')}")
        if len(chunks) > 3:
            print(f"    ... 共 {len(chunks)} 块")

        # ===== 5. EXTRACT（LLM 抽取） =====
        print(f"\n{'='*60}\n  🔬 EXTRACT（Qwen LLM 抽取）\n{'='*60}")
        t0 = time.time()
        all_entities = []
        all_relations = []
        for i, ch in enumerate(chunks):
            ents = self.ner.extract(ch.text)
            # 修正偏移量，记录所属 chunk
            for e in ents:
                if hasattr(e, 'start_char'):
                    e.start_char += ch.start_index
                    e.end_char += ch.start_index   # 修复：end_char 也要加 chunk 起点
                if not e.metadata:
                    e.metadata = {}
                e.metadata["chunk_id"] = i
                e.metadata["source"] = stats["source"]
            all_entities.extend(ents)

            rels = self.rel.extract(ch.text, entities=ents)
            for r in rels:
                if not r.metadata:
                    r.metadata = {}
                r.metadata["chunk_id"] = i
                r.metadata["source"] = stats["source"]
            all_relations.extend(rels)

            print(f"    块{i}: {len(ents)} 实体, {len(rels)} 关系")
        stats["extract_ms"] = round((time.time() - t0) * 1000, 1)
        stats["llm_entities"] = len(all_entities)
        stats["llm_relations"] = len(all_relations)
        print(f"  ✓ LLM 抽取完成: {len(all_entities)} 实体, {len(all_relations)} 关系")

        # ===== 5.5 合并自定义实体 =====
        custom_ents_converted = [{
            "text": ce.text,
            "label": ce.label,
            "confidence": ce.confidence,
            "metadata": {**ce.metadata, "source": "custom_injection"},
            "start_char": 0,
            "end_char": len(ce.text),
        } for ce in self.custom_entities]
        all_entities.extend(custom_ents_converted)
        stats["custom_entities"] = len(custom_ents_converted)
        print(f"  ✓ 合并自定义实体: +{len(custom_ents_converted)}")

        # ===== 6. CONFLICT DETECTION =====
        print(f"\n{'='*60}\n  ⚠️  CONFLICT DETECTION\n{'='*60}")
        t0 = time.time()
        ent_dicts = [{
            "text": e.text if hasattr(e, 'text') else e.get('text'),
            "label": e.label if hasattr(e, 'label') else e.get('label'),
            "confidence": e.confidence if hasattr(e, 'confidence') else e.get('confidence', 1.0),
            "metadata": e.metadata if hasattr(e, 'metadata') else e.get('metadata', {}),
        } for e in all_entities]
        ent_conflicts = self.conflict_detector.detect_entity_conflicts(ent_dicts)
        rel_dicts = [{
            "subject": r.subject.text if hasattr(r.subject, 'text') else r.subject,
            "predicate": r.predicate,
            "object": r.object.text if hasattr(r.object, 'text') else r.object,
            "confidence": r.confidence,
        } for r in all_relations]
        rel_conflicts = self.conflict_detector.detect_relationship_conflicts(rel_dicts)
        stats["conflict_ms"] = round((time.time() - t0) * 1000, 1)
        stats["entity_conflicts"] = len(ent_conflicts)
        stats["relation_conflicts"] = len(rel_conflicts)
        print(f"  ✓ 实体冲突: {len(ent_conflicts)}, 关系冲突: {len(rel_conflicts)}")

        # ===== 7. DEDUPLICATION =====
        print(f"\n{'='*60}\n  🔁 DEDUPLICATION\n{'='*60}")
        t0 = time.time()
        unique_entities = self._dedup_entities(all_entities)
        unique_relations = self._dedup_relations(all_relations)
        stats["dedup_ms"] = round((time.time() - t0) * 1000, 1)
        stats["unique_entities"] = len(unique_entities)
        stats["unique_relations"] = len(unique_relations)
        print(f"  ✓ 去重后: {len(unique_entities)} 实体, {len(unique_relations)} 关系")

        # ===== 8. KG BUILD =====
        print(f"\n{'='*60}\n  🕸️  KG BUILD\n{'='*60}")
        t0 = time.time()
        kg = self.graph_builder.build({
            "entities": unique_entities,
            "relationships": unique_relations,
        })
        stats["kg_build_ms"] = round((time.time() - t0) * 1000, 1)
        n_nodes = len(kg.get("entities", kg.get("nodes", [])))
        n_edges = len(kg.get("relationships", kg.get("edges", [])))
        stats["kg_nodes"] = n_nodes
        stats["kg_edges"] = n_edges
        print(f"  ✓ 图谱构建完成: {n_nodes} 节点, {n_edges} 条边")

        # ===== 9. ONTOLOGY GENERATION =====
        print(f"\n{'='*60}\n  🧠 ONTOLOGY\n{'='*60}")
        t0 = time.time()
        base_ontology = self.onto_gen.generate_ontology({
            "entities": [{
                "text": e.text if hasattr(e, 'text') else e.get('text'),
                "label": e.label if hasattr(e, 'label') else e.get('label'),
            } for e in unique_entities],
            "relationships": [{
                "subject": r.subject.text if hasattr(r.subject, 'text') else r.subject,
                "predicate": r.predicate,
                "object": r.object.text if hasattr(r.object, 'text') else r.object,
            } for r in unique_relations],
        })
        # 合并自定义本体
        final_ontology = {
            **base_ontology,
            "custom_classes": self.custom_ontology_classes,
            "custom_properties": self.custom_ontology_props,
        }
        stats["ontology_ms"] = round((time.time() - t0) * 1000, 1)
        stats["ontology_classes"] = len(final_ontology.get("classes", [])) + len(self.custom_ontology_classes)
        print(f"  ✓ 本体类: {stats['ontology_classes']}")

        # ===== 9.5. REASONING（基于规则的演绎推理） =====
        # 把 unique_relations 转成 Reasoner 能吃的 dict 事实（line 510-514 reasoner.add_fact
        # 对 dict 自动转 "Predicate(source, target)" 字符串），跑 IF-THEN 规则，
        # 推导出的新事实写进 result["reasoning"]["inferred"]。
        # 注意：清空 working memory，避免 LawGraphService 实例复用时残留上一次 process 的 facts。
        print(f"\n{'='*60}\n  🧠 REASONING（基于规则的演绎）\n{'='*60}")
        t0 = time.time()
        inferred: List[Dict[str, str]] = []
        try:
            self.reasoner.facts.clear()  # 复用实例时清掉上次 process 的 facts
            # 喂入事实（KG 中的全部关系）
            for r in unique_relations:
                subj = r.subject.text if hasattr(r.subject, 'text') else r.subject
                obj = r.object.text if hasattr(r.object, 'text') else r.object
                self.reasoner.add_fact({
                    "type": r.predicate,
                    "source_name": str(subj),
                    "target_name": str(obj),
                })
            # 跑推理
            results = self.reasoner.infer_with_results(
                facts=[],  # 上面已经 add_fact 进 working memory
                rules=self.law_rules,
            )
            for inf in results:
                # conclusion 是 "Predicate(arg1, arg2, ...)" 字符串，拆出来
                m = re.match(r"^([^(]+)\(([^,)]+)(?:,\s*([^)]+))?\)$", inf.conclusion.strip())
                if m:
                    pred = m.group(1).strip()
                    a1 = m.group(2).strip()
                    a2 = m.group(3).strip() if m.group(3) else ""
                    inferred.append({
                        "predicate": pred,
                        "subject": a1,
                        "object": a2,
                        "rule_id": inf.rule_used.rule_id if inf.rule_used else None,
                        "rule_name": inf.rule_used.name if inf.rule_used else None,
                        "confidence": inf.confidence,
                        "premises": list(inf.premises),
                    })
                else:
                    inferred.append({
                        "raw": inf.conclusion,
                        "rule_id": inf.rule_used.rule_id if inf.rule_used else None,
                        "confidence": inf.confidence,
                    })
        except Exception as e:
            print(f"  ⚠️  推理失败: {e}")
        stats["reasoning_ms"] = round((time.time() - t0) * 1000, 1)
        stats["n_inferred"] = len(inferred)
        result["reasoning"] = {"inferred": inferred, "rules": self.law_rules}
        print(f"  ✓ 推理出 {len(inferred)} 条新事实（用时 {stats['reasoning_ms']} ms）")
        if inferred:
            for it in inferred[:5]:
                if "subject" in it:
                    print(f"    + {it['subject']} --[{it['predicate']}]--> {it['object']}  "
                          f"via {it.get('rule_name')}")
                else:
                    print(f"    + {it['raw']}")
            if len(inferred) > 5:
                print(f"    ... 共 {len(inferred)} 条")

        # ===== 10. PROVENANCE =====
        print(f"\n{'='*60}\n  📝 PROVENANCE\n{'='*60}")
        prov_count = 0
        for i, e in enumerate(unique_entities[:100]):
            try:
                self.prov_mgr.track_entity(
                    entity_id=f"ent_{i}",
                    source=(e.metadata or {}).get("source", "unknown"),
                    metadata={"text": e.text if hasattr(e, 'text') else e.get('text'),
                              "label": e.label if hasattr(e, 'label') else e.get('label')},
                )
                prov_count += 1
            except Exception:
                pass
        stats["provenance_tracked"] = prov_count
        print(f"  ✓ 溯源记录: {prov_count}")

        # ===== 11. CONTEXT GRAPH =====
        print(f"\n{'='*60}\n  🔗 CONTEXT GRAPH\n{'='*60}")
        for e in unique_entities[:50]:
            try:
                self.ctx_graph.add_node(
                    node_id=e.text if hasattr(e, 'text') else e.get('text'),
                    node_type=e.label if hasattr(e, 'label') else e.get('label'),
                    content=e.text if hasattr(e, 'text') else e.get('text'),
                )
            except Exception:
                pass
        for r in unique_relations[:100]:
            try:
                self.ctx_graph.add_edge(
                    r.subject.text if hasattr(r.subject, 'text') else r.subject,
                    r.object.text if hasattr(r.object, 'text') else r.object,
                    relation=r.predicate,
                )
            except Exception:
                pass
        print(f"  ✓ ContextGraph 节点: {len(unique_entities[:50])}, 边: {len(unique_relations[:100])}")

        # ===== 12. VECTOR STORE（外部向量化，可选） =====
        # 设计原则：所有向量化配置（provider/model/base_url/api_key）由调用方传入，
        # 源码不写死任何路径/provider/model，避免与具体项目耦合。
        if self.enable_vector:
            print(f"\n{'='*60}\n  🧊 VECTOR STORE（外部向量化）\n{'='*60}")
            if not (self.embedding_provider and self.embedding_model):
                print("  💡 跳过：未配置 embedding_provider / embedding_model（外部向量化）")
                print("     如需启用：在 LawGraphService() 传入这两个参数，或设置环境变量")
            else:
                try:
                    from semantica.embeddings import EmbeddingGenerator
                    from semantica.vector_store import FAISSStore
                    import numpy as np

                    # 按 provider 构造 EmbeddingGenerator（参数全部走外部配置）
                    if self.embedding_provider == "openai":
                        eg = EmbeddingGenerator(
                            provider="openai",
                            model=self.embedding_model,
                            base_url=self.embedding_base_url,
                            api_key=self.embedding_api_key,
                        )
                    else:
                        # fastembed / huggingface：走 model 名
                        eg = EmbeddingGenerator(
                            provider=self.embedding_provider,
                            model=self.embedding_model,
                        )

                    texts = [e.text if hasattr(e, 'text') else e.get('text')
                             for e in unique_entities[:50]]
                    vecs = eg.generate_embeddings(texts)
                    vs = FAISSStore(dimension=len(vecs[0]))
                    vs.add_vectors(vecs, texts)
                    result["vector_store"] = vs
                    stats["vector_count"] = len(vecs)
                    print(f"  ✓ 向量化: {len(vecs)} 个实体（provider={self.embedding_provider}）")
                except Exception as e:
                    print(f"  ⚠️  向量化跳过: {e}")
        else:
            print(f"\n💡 Vector Store 未启用（enable_vector=False）")

        # ===== 13. EXPORT =====
        print(f"\n{'='*60}\n  📦 EXPORT\n{'='*60}")
        out_dir = Path("law_kg_output") / datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.lpg_exporter.export(kg, str(out_dir / "knowledge_graph.lpg.json"))
            print(f"  ✓ LPG: knowledge_graph.lpg.json")
        except Exception as e:
            print(f"  ⚠️  LPG 导出失败: {e}")
        # JSON 备份
        with open(out_dir / "entities.json", "w", encoding="utf-8") as f:
            json.dump([{
                "text": e.text if hasattr(e, 'text') else e.get('text'),
                "label": e.label if hasattr(e, 'label') else e.get('label'),
                "confidence": e.confidence if hasattr(e, 'confidence') else e.get('confidence', 1.0),
            } for e in unique_entities], f, ensure_ascii=False, indent=2)
        with open(out_dir / "relations.json", "w", encoding="utf-8") as f:
            json.dump([{
                "subject": r.subject.text if hasattr(r.subject, 'text') else r.subject,
                "predicate": r.predicate,
                "object": r.object.text if hasattr(r.object, 'text') else r.object,
            } for r in unique_relations], f, ensure_ascii=False, indent=2)
        # 本体 JSON-LD
        with open(out_dir / "ontology.jsonld", "w", encoding="utf-8") as f:
            json.dump(final_ontology, f, ensure_ascii=False, indent=2)
        stats["export_dir"] = str(out_dir)
        print(f"  ✓ 导出目录: {out_dir}")

        # ===== 汇总 =====
        stats["total_ms"] = round((time.time() - t_start) * 1000, 1)
        result.update({
            "kg": kg,
            "ontology": final_ontology,
            "entities": unique_entities,
            "relations": unique_relations,
            "chunks": chunks,
            "conflicts": {"entity": ent_conflicts, "relationship": rel_conflicts},
            "stats": stats,
        })

        print(f"\n{'='*60}\n  📊 执行总结\n{'='*60}")
        print(f"  总耗时: {stats['total_ms']} ms")
        print(f"  分块数: {stats['num_chunks']}")
        print(f"  LLM 抽取: {stats['llm_entities']} 实体, {stats['llm_relations']} 关系")
        print(f"  合并自定义: +{stats['custom_entities']} 实体")
        print(f"  去重后: {stats['unique_entities']} 实体, {stats['unique_relations']} 关系")
        print(f"  冲突: {stats['entity_conflicts']} 实体, {stats['relation_conflicts']} 关系")
        print(f"  图谱: {stats['kg_nodes']} 节点, {stats['kg_edges']} 条边")
        print(f"  本体类: {stats['ontology_classes']}")
        print(f"  推理新事实: {stats.get('n_inferred', 0)}")
        print(f"  ✅ 输出目录: {stats['export_dir']}")

        return result

    # ------------------------------------------------------------
    # 批量处理
    # ------------------------------------------------------------
    def process_directory(self, dir_path: str, pattern: str = "*.pdf") -> List[Dict]:
        """批量处理目录下所有匹配的文件"""
        from semantica.ingest import FileIngestor
        ingestor = FileIngestor()
        sources = ingestor.ingest_directory(dir_path, pattern=pattern)
        results = []
        for src in sources:
            print(f"\n\n########## 处理: {src.name} ##########")
            try:
                res = self.process(src.path)
                results.append(res)
            except Exception as e:
                print(f"  ❌ 处理失败: {e}")
        return results

    # ------------------------------------------------------------
    # 内部：去重辅助
    # ------------------------------------------------------------
    def _dedup_entities(self, entities):
        """实体去重（按文本+类型；同 key 取 confidence 最高的）

        修复：原实现运算符优先级错（`or` 右侧 `if-else` 三元会先把低 confidence 覆盖进 seen），
        现在分两步：先用 key 第一次出现作占位，最后统一按 confidence 选最优。
        """
        def _text(e):
            return (e.text if hasattr(e, 'text') else e.get('text', '')).lower().strip()
        def _label(e):
            return e.label if hasattr(e, 'label') else e.get('label', '')
        def _conf(e):
            return e.confidence if hasattr(e, 'confidence') else e.get('confidence', 0) or 0

        seen: Dict[Tuple[str, str], Any] = {}
        for e in entities:
            key = (_text(e), _label(e))
            cur = seen.get(key)
            if cur is None or _conf(e) > _conf(cur):
                seen[key] = e
        return list(seen.values())

    def _dedup_relations(self, relations):
        """关系去重"""
        seen = set()
        unique = []
        for r in relations:
            subj = r.subject.text if hasattr(r.subject, 'text') else r.subject
            obj = r.object.text if hasattr(r.object, 'text') else r.object
            key = (subj.lower(), r.predicate, obj.lower())
            if key not in seen:
                seen.add(key)
                unique.append(r)
        return unique


# ============================================================
# 使用示例
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  Semantica 法律法规生产级知识图谱服务")
    print("=" * 60)

    # ---- 初始化服务 ----
    # API key 一律走环境变量（DASHSCOPE_API_KEY），源码里不接 key
    # 模型仅限 DashScope 公开支持：qwen-turbo / qwen-plus / qwen-max / qwen-long
    svc = LawGraphService(
        qwen_model="qwen-plus",   # 可选: qwen-turbo / qwen-max / qwen-long
        chunk_size=1000,
        chunk_overlap=200,
        enable_vector=False,      # 需要向量化改为 True，并填 embedding_provider/embedding_model
    )

    # ---- 动态扩展（随时可加） ----
    # print("\n--- 动态注册自定义实体类型 ---")
    # svc.add_entity_type("REGULATION", "行政法规")
    # svc.add_entity_type("JUDICIAL_INTERPRETATION", "司法解释")

    # print("\n--- 动态添加自定义实体 ---")
    # svc.add_entity("《网络安全法》", "LAW", metadata={"effective_date": "2017-06-01"})
    # svc.add_entity("（2023）京01民初123号", "CASE", metadata={"court": "北京市第一中级人民法院"})
    # svc.add_entity("数据出境安全评估", "LEGAL_CONCEPT", metadata={"category": "数据合规"})

    # print("\n--- 动态扩展本体 ---")
    # svc.add_ontology_class("NetworkSecurityLaw", "网络安全法", parent="Law")
    # svc.add_ontology_class("DataExport", "数据出境", parent="LegalConcept")
    # svc.add_ontology_property("requiresSecurityAssessment", "DataExport", "boolean")

    # ---- 运行管线 ----
    # sample_text = """
    # 北京智云科技有限公司（以下简称"智云公司"）与上海数据服务有限公司
    # （以下简称"数据公司"）于2023年5月签订《数据委托处理协议》，
    # 约定数据公司为智云公司提供用户行为数据的清洗和分析服务，
    # 合同期限一年，违约金为合同总额的30%。
    
    # 2023年10月，智云公司发现数据公司未经同意将部分用户数据
    # 提供给第三方合作机构，涉嫌违反《个人信息保护法》第21条
    # 关于委托处理的规定。智云公司认为数据公司构成根本违约，
    # 依据《民法典》第577条主张违约责任，要求解除合同并支付违约金。
    
    # 数据公司辩称：其行为属于《民法典》第590条规定的不可抗力情形，
    # 且合同约定的30%违约金过高，请求法院予以调减。
    
    # 北京市第一中级人民法院经审理认为：
    # 数据公司的行为不构成不可抗力，但合同约定的30%违约金确实过高。
    # 法院参照《最高人民法院关于适用〈中华人民共和国民法典〉
    # 合同编通则若干问题的解释》第65条，将违约金调减至合同总额的15%。
    # 判决生效后，双方均未上诉。
    
    # 另查明，数据公司向境外关联公司传输中国用户数据的行为，
    # 未通过国家网信部门组织的数据出境安全评估，
    # 违反了《数据安全法》第31条及《个人信息保护法》第38条的规定。
    # """

    
    sample_text = r"D:\workspace\semantica_demo\rules\业务规则\大众点评\大众点评基础规则\未分类\大众点评POI信息发布和认领规则.pdf"
    result = svc.process(sample_text)

    # ---- 查看结果 ----
    print("\n" + "=" * 60)
    print("  📋 图谱实体预览（前 20）")
    print("=" * 60)
    for i, e in enumerate(result["entities"][:20]):
        text = e.text if hasattr(e, 'text') else e.get('text')
        label = e.label if hasattr(e, 'label') else e.get('label')
        print(f"  {i+1:>2}. [{label:<18}] {text}")

    print("\n" + "=" * 60)
    print("  📋 图谱关系预览（前 15）")
    print("=" * 60)
    for i, r in enumerate(result["relations"][:15]):
        subj = r.subject.text if hasattr(r.subject, 'text') else r.subject
        obj = r.object.text if hasattr(r.object, 'text') else r.object
        print(f"  {i+1:>2}. {subj} --[{r.predicate}]--> {obj}")

    print("\n" + "=" * 60)
    print("  📋 自定义本体类")
    print("=" * 60)
    for cls in result["ontology"].get("custom_classes", []):
        parent = cls.get('parent') or '（顶层）'
        print(f"  - {cls['label']:<20} [{cls['id']}]  父类: {parent}")

    print("\n✅ 法律法规知识图谱构建完成！")
    print(f"📁 输出目录: {result['stats']['export_dir']}")