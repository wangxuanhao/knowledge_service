"""项目范围内的 RDF/OWL 与 SHACL 解释；不做网络查询。"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, unquote

from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.collection import Collection
from rdflib.namespace import OWL, SH, XSD

from ..utils.attributes import primitive_datatype
from .ontology_iri import valid_application_iri
from .ontology_vocabulary import (
    RevisionGraph,
    deprecated_marker_is_true,
    index_governed_vocabulary,
    local_name,
)

DATA = Namespace("urn:knowledge:")


class UnknownOntologyTerm(ValueError):
    """本体里查不到这个术语（或存在歧义）。

    单独成类是为了让调用方能**只按这一种失败**做判断：C1「结构待定」收下这类知识，
    其余失败（约束、端点、datatype）照旧拒绝 —— 用中文消息字符串做判断太脆。
    """

    def __init__(self, name: str, expected=None):
        super().__init__(f"未知或存在歧义的本体术语：{name}")
        self.term = name
        self.expected = expected


def readable_iri_segment(value: str) -> str:
    """返回稳定的 Unicode IRI 片段，同时转义保留标点。"""
    characters = []
    for character in unicodedata.normalize('NFC', str(value).strip()):
        category = unicodedata.category(character)
        if category[0] in ('L', 'N') or category.startswith('M') or character in '._~-':
            characters.append(character)
        elif character.isspace():
            characters.append('-')
        else:
            characters.append(quote(character, safe=''))
    segment = re.sub(r'-+', '-', ''.join(characters)).strip('-')
    if not segment:
        raise ValueError('术语名必须包含可用 IRI 字符')
    return segment


def generated_term_iri(project_id: str, label: str) -> str:
    """构建每个新本体术语所使用的规范化项目级 IRI。"""
    return f'urn:knowledge:ontology:{project_id}:{readable_iri_segment(label)}'


def parse_shacl_focus_records(shacl_report_text: str, known_ids: set[str]) -> set[str]:
    """从与已知记录匹配的 SHACL focus-node IRI 中提取记录 ID。

    focus 节点形如 <urn:knowledge:doc_id%3Achunk%3A0%3Aentity_xxx>，
    其中 DATA = Namespace("urn:knowledge:")，id 经过 quote(r['id'], safe='') 编码。
    """
    prefix = str(DATA)
    found: set[str] = set()
    for match in re.finditer(re.escape(prefix) + r'([^>]+)', shacl_report_text):
        token = match.group(1)
        try:
            decoded = unquote(token)
        except Exception:
            continue
        if decoded in known_ids:
            found.add(decoded)
    return found


def _record_id(focus):
    value=str(focus or '')
    prefix=str(DATA)
    return unquote(value[len(prefix):]) if value.startswith(prefix) else None


def _shacl_violations(results_graph):
    """将 pySHACL 结果节点转换为稳定、UI 安全的审计字段。"""
    violations=[]
    for result in results_graph.subjects(RDF.type,SH.ValidationResult):
        path=results_graph.value(result,SH.resultPath)
        component=results_graph.value(result,SH.sourceConstraintComponent)
        severity=results_graph.value(result,SH.resultSeverity)
        message=results_graph.value(result,SH.resultMessage)
        value=results_graph.value(result,SH.value)
        violations.append({
            'record_id':_record_id(results_graph.value(result,SH.focusNode)),
            'constraint':local_name(component) if component else 'SHACLConstraint',
            'path':str(path) if path else None,
            'path_label':local_name(path) if path else None,
            'severity':local_name(severity) if severity else 'Violation',
            'message':str(message or '本体约束校验未通过'),
            'value':str(value) if value is not None else None,
        })
    return sorted(violations,key=lambda item:(
        item.get('record_id') or '',item.get('path') or '',item.get('constraint') or '',item.get('message') or ''))


def match_query_expansion(index, query, *, min_length=2, max_terms=8):
    """在查询扩展索引上把查询串匹配成「命中类 + 其全部子类」（P0-1 读路径）。

    中文没有空格，所以这里用**子串匹配**而不是分词：类的某个表层形式出现在查询串里
    就算命中。两条纪律：

    * **长度阈值**：短于 ``min_length`` 的形式丢弃 —— 单个汉字（"方"、"人"）几乎
      必然误命中；
    * **最长匹配**：多个命中的形式互相包含时，只保留最长的那一个
      （查询 "合作方有哪些" 命中 "合作方"；被它包含的 "方" 不再单独成词）。

    返回的四个键是给检索层用的：
      * ``terms``：命中类 IRI；
      * ``labels``：命中类与子类的业务标签（关键词通道 OR 用，已按 max_terms 截断）；
      * ``subclasses``：命中类**除自身以外**的全部子类 IRI（子类蕴含）；
      * ``type_iris``：``terms`` + ``subclasses``，也就是类型下推要认的集合。
    """
    empty = {'terms': [], 'labels': [], 'subclasses': [], 'type_iris': []}
    needle = (query or '').casefold().strip()
    if not needle:
        return empty
    matched_forms = {}
    for form, class_id in index['surfaces']:
        if len(form) >= min_length and form.casefold() in needle:
            matched_forms.setdefault(form, set()).add(class_id)
    if not matched_forms:
        return empty
    # 最长匹配：被更长的命中形式包含的短形式丢掉。同长度按字典序，保证确定性。
    ordered = sorted(matched_forms, key=lambda form: (-len(form), form))
    kept = {form for position, form in enumerate(ordered)
            if not any(form in longer for longer in ordered[:position])}
    # 命中过多说明查询太泛：按"匹配到的形式长度"取最具体的若干个。
    specificity = {}
    for form in kept:
        for class_id in matched_forms[form]:
            specificity[class_id] = max(specificity.get(class_id, 0), len(form))
    terms = sorted(sorted(specificity, key=lambda cid: (-specificity[cid], cid))[:max_terms])
    term_set = set(terms)
    ancestors = index['ancestors']
    subclasses = sorted(
        class_id for class_id, parents in ancestors.items()
        if class_id not in term_set and parents & term_set)
    type_iris = [*terms, *subclasses]
    labels = [index['labels'].get(class_id) for class_id in type_iris]
    return {
        'terms': terms,
        'labels': sorted({label for label in labels if label})[:max_terms],
        'subclasses': subclasses,
        'type_iris': type_iris,
    }


class Ontology:
    def __init__(self, turtle: str):
        self.graph = RevisionGraph()
        try:
            self.graph.parse(data=turtle, format="turtle")
        except Exception as exc:
            raise ValueError(f"无效的 Turtle：{exc}") from exc
        self._governed_records = {}
        self.classes = set()
        self.relations = set()
        self.attributes = set()
        self._indexed_graph_revision = None
        self._refresh_vocabulary()

    def _refresh_vocabulary(self):
        if self._indexed_graph_revision == self.graph.revision:
            return
        records = index_governed_vocabulary(self.graph)
        self._governed_records = {record.node: record for record in records}
        for target, kind in (
                (self.classes, "class"),
                (self.relations, "relation"),
                (self.attributes, "attribute")):
            target.clear()
            target.update(record.node for record in records if kind in record.kinds)
        self._indexed_graph_revision = self.graph.revision

    def _resolve_current(self, name: str, allowed):
        matches = [uri for uri in allowed if str(uri) == name or local_name(uri) == name]
        if len(matches) != 1:
            raise UnknownOntologyTerm(name, expected=allowed)
        return matches[0]

    def resolve(self, name: str, allowed=None):
        self._refresh_vocabulary()
        allowed = allowed if allowed is not None else self.classes | self.relations | self.attributes
        return self._resolve_current(name, allowed)

    def is_active_term(self, term):
        """Return false only for an explicitly truthy ``owl:deprecated`` marker."""
        self._refresh_vocabulary()
        try:
            node = term if isinstance(term, URIRef) else self._resolve_current(
                str(term), self.classes | self.relations | self.attributes)
        except ValueError:
            return False
        record = self._governed_records.get(node)
        if record is not None:
            return record.active
        for marker in self.graph.objects(node, OWL.deprecated):
            if deprecated_marker_is_true(marker):
                return False
        return True

    def summary(self, active_only=False):
        self._refresh_vocabulary()

        def item(uri):
            record = self._governed_records[uri]
            labels = record.label_values
            zh = en = plain = ''
            for obj in labels:
                lang = getattr(obj, 'language', '') or ''
                val = str(obj)
                if not zh and lang.lower().startswith('zh'):
                    zh = val
                elif not en and lang.lower().startswith('en'):
                    en = val
                elif not plain and not lang:
                    plain = val
            return {"id": str(uri), "name": local_name(uri),
                    "label": plain or en or local_name(uri),
                    "label_zh": zh, "label_en": en,
                    "description": str(self.graph.value(uri, RDFS.comment) or ""),
                    "active": record.active}
        visible = lambda values: [value for value in values
                                  if not active_only or self._governed_records[value].active]
        return {"classes": [{**item(c), "parents": [str(p) for p in self.graph.objects(c, RDFS.subClassOf)]}
                            for c in sorted(visible(self.classes))],
                "relations": [{**item(p), "domain": [str(v) for v in self.constraint_types(p,RDFS.domain)],
                               "range": [str(v) for v in self.constraint_types(p,RDFS.range)]}
                              for p in sorted(visible(self.relations))],
                "attributes": [{**item(p), "domain": [str(v) for v in self.constraint_types(p,RDFS.domain)],
                                "range": [str(v) for v in self.graph.objects(p, RDFS.range)]}
                               for p in sorted(visible(self.attributes))], "triples": len(self.graph)}

    def constraint_types(self,predicate,constraint):
        """展开用作允许端点集的命名类或 OWL 并集。"""
        values=[]
        for value in self.graph.objects(predicate,constraint):
            head=self.graph.value(value,OWL.unionOf)
            values.extend(list(Collection(self.graph,head)) if head else [value])
        return values

    def parents(self, term):
        seen, pending = {term}, [term]
        while pending:
            for parent in self.graph.objects(pending.pop(), RDFS.subClassOf):
                if parent not in seen:
                    seen.add(parent)
                    pending.append(parent)
        return seen

    def expansion_index(self, active_only=True):
        """构建查询扩展索引：类的表层形式 + 业务标签 + **祖先闭包**（P0-1）。

        单独抽出来的原因：索引只随本体版本变化，而查询是逐次来的。
        调用方（检索服务）可以按本体版本签名缓存它，避免每个请求重新解析 Turtle。

        为什么缓存里要放"祖先闭包"而不是直接放子类：子类关系是反着查的
        （拿到命中类后要枚举"谁是它的后代"），正向的祖先集合让这一步变成一次
        字典扫描，而不用对每个类都走一遍图。
        """
        summary = self.summary(active_only=active_only)
        surfaces, labels, direct_parents = [], {}, {}
        for item in summary['classes']:
            class_id = item['id']
            label = (item.get('label_zh') or item.get('label')
                     or item.get('label_en') or item.get('name') or '')
            labels[class_id] = label or item.get('name') or class_id
            direct_parents[class_id] = {
                str(parent) for parent in item.get('parents') or ()}
            for form in {item.get('label_zh') or '', item.get('label') or '',
                         item.get('label_en') or '', item.get('name') or ''}:
                form = form.strip()
                if form:
                    surfaces.append((form, class_id))
        # 直接父类按**声明顺序**留一份列表（不是集合）：给用户看"父类：A → B"时顺序要稳定，
        # 集合的迭代顺序在不同进程里可能不一样，界面会跟着闪。
        ordered_parents = {}
        for item in summary['classes']:
            ordered_parents[item['id']] = [str(parent) for parent in item.get('parents') or ()]
        ancestors = {}
        for class_id in list(direct_parents):
            seen, pending = set(), [class_id]
            while pending:
                current = pending.pop()
                if current not in direct_parents:
                    # 受治理类之外的中间类（例如上层引用的共享父类）也要能继续往上走。
                    direct_parents[current] = {
                        str(parent) for parent in
                        self.graph.objects(URIRef(current), RDFS.subClassOf)}
                for parent in direct_parents[current]:
                    if parent not in seen:
                        seen.add(parent)
                        pending.append(parent)
            ancestors[class_id] = frozenset(seen)
        return {'surfaces': surfaces, 'labels': labels, 'ancestors': ancestors,
                'parents': ordered_parents}

    def query_expansion(self, query, *, min_length=2, max_terms=8):
        """把查询串扩展成「命中类 + 其全部子类」的术语与类型集合（P0-1）。

        用途：查「合作方」时要能命中 ``type=签约主播`` 的实体（子类蕴含生效）。
        匹配规则（长度阈值、最长匹配）见 :func:`match_query_expansion`。
        """
        return match_query_expansion(
            self.expansion_index(), query, min_length=min_length, max_terms=max_terms)

    def relation_constraint_issues(self,predicate,subject_type,object_type):
        """返回 RDFS domain/range 不匹配，而不把 OWL 推理当作校验。"""
        predicate=self.resolve(predicate,self.relations)
        actual={'subject_id':self.resolve(subject_type,self.classes),'object_id':self.resolve(object_type,self.classes)}
        issues=[]
        for key,constraint in [('subject_id',RDFS.domain),('object_id',RDFS.range)]:
            expected=self.constraint_types(predicate,constraint)
            if expected and not any(term in self.parents(actual[key]) for term in expected):
                issues.append({'endpoint':key,'actual_type':str(actual[key]),'expected_types':[str(x) for x in expected]})
        return issues

    def attribute_max_count_one(self, predicate, subject_type=None):
        """Whether a matching SHACL property shape declares ``sh:maxCount 1``."""
        predicate = self.resolve(predicate, self.attributes)
        actual_types = None
        if subject_type is not None:
            actual_types = self.parents(self.resolve(subject_type, self.classes))
        for shape in self.graph.subjects(SH.path, predicate):
            maximum = self.graph.value(shape, SH.maxCount)
            try:
                if maximum is None or int(maximum) != 1:
                    continue
            except (TypeError, ValueError):
                continue
            owners = set(self.graph.subjects(SH.property, shape))
            targets = set(self.graph.objects(shape, SH.targetClass))
            for owner in owners:
                targets.update(self.graph.objects(owner, SH.targetClass))
            if actual_types is None or not targets or targets & actual_types:
                return True
        return False

    def dataset(self, records):
        graph = Graph()
        for prefix, ns in self.graph.namespaces():
            graph.bind(prefix, ns)
        graph += self.graph
        formal_attribute_pairs = set()
        for r in records:
            if (r['kind'] != 'attribute' or
                    r.get('metadata', {}).get('_deleted')):
                continue
            try:
                predicate = self.resolve(r.get('type', ''), self.attributes)
            except ValueError:
                continue
            formal_attribute_pairs.add((r.get('subject_id'), predicate))
        for r in records:
            if r['kind'] != 'entity':
                continue
            node = DATA[quote(r['id'], safe='')]
            try:
                term = self.resolve(r.get('type', ''), self.classes)
            except ValueError:
                continue
            for parent in self.parents(term):
                graph.add((node, RDF.type, parent))
            graph.add((node, RDFS.label, Literal(r.get('text', ''))))
            for field, value in r.get('properties', {}).items():
                try:
                    predicate = self.resolve(field, self.attributes)
                except ValueError:
                    if not field.startswith(('http://', 'https://', 'urn:')):
                        continue
                    predicate = URIRef(field)
                if (r['id'], predicate) in formal_attribute_pairs:
                    continue
                for v in value if isinstance(value, list) else [value]:
                    if isinstance(v, (str, int, float, bool)):
                        graph.add((node, predicate, Literal(v)))
        ids = {r['id'] for r in records if r['kind'] == 'entity'}
        for r in records:
            if (r['kind'] != 'attribute' or r.get('subject_id') not in ids or
                    r.get('metadata', {}).get('_deleted')):
                continue
            try:
                predicate = self.resolve(r.get('type', ''), self.attributes)
                datatype = primitive_datatype(r.get('value'))
                if r.get('datatype') != datatype:
                    continue
            except ValueError:
                continue
            graph.add((DATA[quote(r['subject_id'], safe='')], predicate,
                       Literal(r['value'], datatype=URIRef(datatype))))
        for r in records:
            if r['kind'] == 'relation' and r.get('subject_id') in ids and r.get('object_id') in ids:
                try:
                    predicate = self.resolve(r.get('type', ''), self.relations)
                except ValueError:
                    continue
                graph.add((DATA[quote(r['subject_id'], safe='')], predicate, DATA[quote(r['object_id'], safe='')]))
        return graph

    def validate(self, records, shacl=True, enforce_relationship_constraints=True):
        errors = []
        violations = []
        entities = {r['id']: r for r in records if r['kind'] == 'entity'}
        for r in records:
            try:
                if r['kind'] == 'entity':
                    self.resolve(r.get('type', ''), self.classes)
                elif r['kind'] == 'relation':
                    predicate = self.resolve(r.get('type', ''), self.relations)
                    for key, constraint in [('subject_id', RDFS.domain), ('object_id', RDFS.range)]:
                        endpoint = entities.get(r.get(key))
                        if not endpoint:
                            raise ValueError(f"缺少关系端点：{r.get(key)}")
                        term = self.resolve(endpoint.get('type', ''), self.classes)
                        expected=self.constraint_types(predicate,constraint)
                        if enforce_relationship_constraints and expected and not any(
                                allowed in self.parents(term) for allowed in expected):
                            raise ValueError(f"{key} 类型 {term} 不满足 {constraint} "
                                f"中的任何一个：{', '.join(str(value) for value in expected)}")
                elif r['kind'] == 'attribute':
                    predicate = self.resolve(r.get('type', ''), self.attributes)
                    subject = entities.get(r.get('subject_id'))
                    if not subject:
                        raise ValueError(f"缺少属性主体：{r.get('subject_id')}")
                    subject_type = self.resolve(subject.get('type', ''), self.classes)
                    domains = self.constraint_types(predicate, RDFS.domain)
                    if domains and not any(
                            allowed in self.parents(subject_type) for allowed in domains):
                        raise ValueError(
                            f"subject_id 类型 {subject_type} 不满足属性 domain 中的任何一个："
                            f"{', '.join(str(value) for value in domains)}")
                    datatype = primitive_datatype(r.get('value'))
                    if r.get('datatype') != datatype:
                        raise ValueError('属性 datatype 必须与值的原始类型一致')
                    ranges = self.constraint_types(predicate, RDFS.range)
                    if ranges and URIRef(datatype) not in ranges and RDFS.Literal not in ranges:
                        raise ValueError(
                            f"属性值类型 {datatype} 不满足 range 中的任何一个："
                            f"{', '.join(str(value) for value in ranges)}")
            except UnknownOntologyTerm as exc:
                # 机器可读的失败原因（C1）：页面和写入侧要能只挑出"缺术语"这一类，
                # 而不是去匹配中文消息。
                errors.append({"record_id": r['id'], "message": str(exc),
                               "code": "unknown_term", "term": exc.term})
            except ValueError as exc:
                errors.append({"record_id": r['id'], "message": str(exc)})
        report = ''
        if shacl and not errors:
            from pyshacl import validate
            conforms, results_graph, report = validate(self.dataset(records), shacl_graph=self.graph,
                                          inference='rdfs', advanced=False, do_owl_imports=False)
            if not conforms:
                violations=_shacl_violations(results_graph)
                errors.extend(violations or [{"record_id": None, "message": str(report)}])
        return {"conforms": not errors, "errors": errors, "violations":violations, "report": str(report)}

    def validate_timeline(self, records, enforce_relationship_constraints=True):
        """SHACL 只看到共存事实，在每个成员变化的区间各校验一次。"""
        from ..core.time import normalize_time, utc_now
        rows = [{**r, 'valid_from': normalize_time(r.get('valid_from')),
                 'valid_until': normalize_time(r.get('valid_until'))} for r in records]
        boundaries = sorted({r[key] for r in rows for key in ('valid_from', 'valid_until') if r[key]})
        instants = set(boundaries)
        if boundaries:
            earliest = datetime.fromisoformat(boundaries[0].replace('Z', '+00:00'))
            if earliest > datetime.min.replace(tzinfo=timezone.utc):
                instants.add(normalize_time(earliest - timedelta(microseconds=1)))
        else:
            instants.add(utc_now())
        for instant in sorted(instants):
            active = [r for r in rows if (not r['valid_from'] or r['valid_from'] <= instant)
                      and (not r['valid_until'] or instant < r['valid_until'])]
            result = self.validate(active,enforce_relationship_constraints=enforce_relationship_constraints)
            if not result['conforms']:
                return {**result, 'valid_at': instant}
        return {'conforms': True, 'errors': [], 'violations':[], 'segments_checked': len(instants)}

    def query(self, records, query):
        # 拒绝远程数据集/联邦与更新，即使查询以 PREFIX 开头。
        if re.search(r'\b(SERVICE|FROM|LOAD|INSERT|DELETE|CLEAR|CREATE|DROP|MOVE|COPY|ADD)\b', query, re.I):
            raise ValueError("只允许本地 SELECT/ASK 查询")
        from rdflib.plugins.sparql.parser import parseQuery
        try:
            parsed = parseQuery(query)
            if parsed[1].name not in ('SelectQuery', 'AskQuery'):
                raise ValueError("仅支持 SELECT/ASK")
            result = self.dataset(records).query(query)
        except Exception as exc:
            raise ValueError(f"无效的本地 SPARQL：{exc}") from exc
        if result.type == 'ASK':
            return {"type": "ASK", "value": bool(result.askAnswer)}
        rows = []
        for row in result:
            if len(rows) >= 1000:
                break
            rows.append({str(k): str(v) if v is not None else None for k, v in zip(result.vars, row)})
        return {"type": "SELECT", "columns": [str(v) for v in result.vars], "rows": rows, "limit": 1000}


# --------------------------------------------------------------------------
# 本体术语工具（由 api.workspace 本体维护与 api.ontology_changes 本体变更共享）
# --------------------------------------------------------------------------

def absolute_iri(value):
    """判断一个值是否为可用作 IRI 的绝对地址（URN/HTTP(S)，且无非法空白/保留字符）。"""
    return valid_application_iri(value)


def term_kind(ontology, node):
    """返回术语在本体中的类别：class / relation / attribute，未定义则 None。"""
    ontology._refresh_vocabulary()
    if node in ontology.classes:
        return 'class'
    if node in ontology.relations:
        return 'relation'
    if node in ontology.attributes:
        return 'attribute'
    return None


def term_impact(service, p, uri, ontology):
    """统计该术语被哪些知识记录引用、被哪些约束/待审核候选关联（用于停用/调整前评估）。

    受影响记录分两类：
    - 直接命中（via='type'）：记录自身 type 就是这个术语，或属性键属于该属性类型；
    - 端点连带（via='endpoint'）：关系的 subject/object 指向的实体被直接命中——
      停用实体类时这才是大头（关系自己的 type 是关系类型，不是端点类型），
      只统计直接命中会让界面显示"关系 0 条"，掩盖真实后果。
    """
    node = URIRef(uri)
    rows = service.repository.current_records(p)
    direct = [r for r in rows if r.get('type') == uri or uri in r.get('properties', {})]
    direct_ids = {r['id'] for r in direct}
    linked = []
    for r in rows:
        if r.get('kind') != 'relation' or r['id'] in direct_ids:
            continue
        if r.get('subject_id') in direct_ids or r.get('object_id') in direct_ids:
            linked.append(r)
    records = [*direct, *linked]
    via = {r['id']: 'type' for r in direct}
    via.update({r['id']: 'endpoint' for r in linked})
    constraints = [{'subject': str(s), 'predicate': str(pred)}
                   for s, pred in ontology.graph.subject_predicates(node)]
    pending = []
    for doc in rows:
        for candidate in doc.get('metadata', {}).get('review_candidates', []):
            names = {candidate.get(k) for k in ('target_type', 'proposed_type', 'predicate')}
            if candidate.get('status') == 'pending' and ({uri, local_name(uri)} & names):
                pending.append(candidate['id'])
    # 分类计数：三个键恒定存在（前端可直接展示，无需兜底判断）
    kind_counts = {'entity': 0, 'relation': 0, 'attribute': 0}
    for r in records:
        kind = r.get('kind')
        if kind in kind_counts:
            kind_counts[kind] += 1
    # 可读明细：ID 对人无用，给出"名称 + 类别 + 受影响方式"，前端按类别分组展示
    record_preview = [{'id': r['id'], 'kind': r.get('kind'), 'via': via[r['id']],
                       'text': (r.get('text') or '').strip()}
                      for r in records[:50]]
    return {'record_count': len(records), 'record_ids': [r['id'] for r in records[:50]],
            'record_ids_truncated': len(records) > 50,
            'kind_counts': kind_counts, 'record_preview': record_preview,
            'linked_relation_count': len(linked),
            'constraint_count': len(constraints), 'constraints': constraints[:50],
            'pending_review_count': len(pending), 'pending_review_ids': pending[:50]}


def relation_usage(service, p, uri, ontology):
    """这个关系类型「本体允许怎么连」与「实际怎么连」——回填 rdfs:domain/range 的唯一口径。

    为什么需要它（用户反馈「关系没办法显示」）：知识写入抽出来的关系类型常常只写了名字，
    没有 rdfs:domain / rdfs:range。而画布只在**两端都在图里**时才画得出一条关系线，
    于是界面上 9 个关系类型一条线都没有，用户以为关系丢了。实测某个真项目：
    9 个关系类型 domain/range 全空 → 画布只画出 1 条继承线、0 条关系线。

    这里**只统计、不判断对错、不写任何数据**：从已经入库的实例关系反推两端实际用到的类，
    作为「可回填的候选」交给用户核对；写回走草案命令通道（add_domain / add_range），
    仍然要审核、要发布。数字一律来自 current_records —— 界面不许自己算，否则两个口径必然打架。

    返回：
      declared  本体现在声明的 domain/range（可能为空，这正是问题本身）
      used      该关系类型的实例条数（0 表示还没有知识用它，回填无从谈起）
      inferred  按实际用量降序的候选类（带 count 与中文标签），可直接摆给用户核对
      samples   前若干条「主体·类型 → 客体·类型」样例，供人判断推断是否合理
      missing   domain / range 是否还没声明（前端据此决定要不要给回填入口）
    """
    rows = service.repository.current_records(p)
    entities = [r for r in rows if r.get('kind') == 'entity']
    entity_type = {r['id']: r.get('type') for r in entities}
    entity_text = {r['id']: (r.get('text') or '').strip() for r in entities}
    # 只有 kind='relation' 的记录才是实例关系；同型记录（文档/属性）不参与推断。
    relations = [r for r in rows if r.get('kind') == 'relation' and r.get('type') == uri]

    # 术语 IRI → 中文名：只在本体里查，查不到就退回 IRI 末段（不编名字）。
    # 一次建好索引再查：summary() 是 O(词表) 的，放在循环里按术语各查一遍太浪费
    # （一条关系有几十条实例时会被调用到十几次）。
    label_index = {}

    def term_label(term_iri):
        if not term_iri:
            return ''
        if not label_index:
            for group in ('classes', 'relations', 'attributes'):
                for item in ontology.summary().get(group, []):
                    label_index[item['id']] = (item.get('label_zh') or item.get('label')
                                               or item.get('name') or local_name(item['id']))
        return label_index.get(term_iri) or local_name(term_iri)

    def tally(side):
        counts = {}
        for row in relations:
            type_iri = entity_type.get(row.get(side))
            if type_iri:
                counts[type_iri] = counts.get(type_iri, 0) + 1
        return sorted(({'iri': iri, 'label': term_label(iri), 'count': n}
                       for iri, n in counts.items()),
                      key=lambda item: (-item['count'], item['label']))

    node = URIRef(uri)
    declared = {'domain': [str(v) for v in ontology.constraint_types(node, RDFS.domain)],
                'range': [str(v) for v in ontology.constraint_types(node, RDFS.range)]}
    inferred = {'domain': tally('subject_id'), 'range': tally('object_id')}
    samples = [{
        'subject': entity_text.get(row.get('subject_id'), ''),
        'subject_type': term_label(entity_type.get(row.get('subject_id'))),
        'predicate': term_label(uri),
        'object': entity_text.get(row.get('object_id'), ''),
        'object_type': term_label(entity_type.get(row.get('object_id'))),
    } for row in relations[:5]]
    return {
        'uri': uri,
        'label': term_label(uri),
        'declared': declared,
        'used': len(relations),
        'inferred': inferred,
        'samples': samples,
        'missing': {'domain': not declared['domain'], 'range': not declared['range']},
    }


def set_term_constraints(ontology, node, kind, parent='', domain='', range_='',
                         domains=None, ranges=None, parents=None):
    """Compatibility wrapper over governed multi-value operation semantics."""
    from .ontology_operations import apply_operations, build_operation

    snapshot = Ontology(ontology.graph.serialize(format='turtle'))
    target = str(node)
    operations = []
    if kind == 'class':
        requested = list(parents if parents else ([parent] if parent else []))
        if len(requested) != len(set(requested)):
            raise ValueError('父类不能重复')
        resolved = [str(snapshot.resolve(value, snapshot.classes)) for value in requested]
        existing = [str(value) for value in snapshot.graph.objects(node, RDFS.subClassOf)]
        operations.extend(build_operation(
            'remove_parent', target, before={'value': value}) for value in existing)
        operations.extend(build_operation(
            'add_parent', target, after={'value': value}) for value in resolved)
    elif kind in ('relation', 'attribute'):
        domain_values = [value for value in
                         (domains if domains else ([domain] if domain else []))
                         if value]
        if len(domain_values) != len(set(domain_values)):
            raise ValueError('domain 约束不能重复')
        resolved_domains = [str(snapshot.resolve(value, snapshot.classes))
                            for value in domain_values]
        existing_domains = [str(value) for value in snapshot.constraint_types(
            node, RDFS.domain)]
        operations.extend(build_operation(
            'remove_domain', target, before={'value': value})
            for value in existing_domains if value not in resolved_domains)
        operations.extend(build_operation(
            'add_domain', target, after={'value': value})
            for value in resolved_domains if value not in existing_domains)
        if kind == 'relation':
            range_values = [value for value in
                            (ranges if ranges else ([range_] if range_ else []))
                            if value]
            if len(range_values) != len(set(range_values)):
                raise ValueError('range 约束不能重复')
            resolved_ranges = [str(snapshot.resolve(value, snapshot.classes))
                               for value in range_values]
            existing_ranges = [str(value) for value in snapshot.constraint_types(
                node, RDFS.range)]
            operations.extend(build_operation(
                'remove_range', target, before={'value': value})
                for value in existing_ranges if value not in resolved_ranges)
            operations.extend(build_operation(
                'add_range', target, after={'value': value})
                for value in resolved_ranges if value not in existing_ranges)
        else:
            datatype = URIRef(range_) if range_ else None
            existing_datatypes = [str(value) for value in snapshot.constraint_types(
                node, RDFS.range)]
            if len(existing_datatypes) > 1:
                raise ValueError('DatatypeProperty 当前数据类型定义不唯一')
            operations.append(build_operation(
                'set_datatype', target,
                before={'datatype': existing_datatypes[0]
                        if existing_datatypes else None},
                after={'datatype': str(datatype) if datatype else None}))
    else:
        raise ValueError('不支持的本体术语类型')
    ontology.__init__(apply_operations(
        ontology.graph.serialize(format='turtle'), operations))
