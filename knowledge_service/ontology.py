"""项目范围内的 RDF/OWL 与 SHACL 解释；不做网络查询。"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, unquote

from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.collection import Collection
from rdflib.namespace import OWL, SH

DATA = Namespace("urn:knowledge:")


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


def local_name(uri):
    value = str(uri).rsplit("#", 1)[-1].rsplit("/", 1)[-1]
    # 项目本体使用 URN（urn:knowledge:ontology:<project>:Term）。
    # URN 没有斜杠/井号片段，因此旧实现会把整个存储标识符泄漏到
    # 每个面向用户的名称和描述中。
    return value.rsplit(":", 1)[-1] if value.startswith("urn:") else value


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


class Ontology:
    def __init__(self, turtle: str):
        self.graph = Graph()
        try:
            self.graph.parse(data=turtle, format="turtle")
        except Exception as exc:
            raise ValueError(f"无效的 Turtle：{exc}") from exc
        self.classes = set(self.graph.subjects(RDF.type, OWL.Class)) | set(self.graph.subjects(RDF.type, RDFS.Class))
        self.relations = set(self.graph.subjects(RDF.type, OWL.ObjectProperty))
        self.attributes = set(self.graph.subjects(RDF.type, OWL.DatatypeProperty))

    def resolve(self, name: str, allowed=None):
        allowed = allowed if allowed is not None else self.classes | self.relations | self.attributes
        matches = [uri for uri in allowed if str(uri) == name or local_name(uri) == name]
        if len(matches) != 1:
            raise ValueError(f"未知或存在歧义的本体术语：{name}")
        return matches[0]

    def summary(self):
        def item(uri):
            labels = list(self.graph.objects(uri, RDFS.label))
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
                    "description": str(self.graph.value(uri, RDFS.comment) or "")}
        return {"classes": [{**item(c), "parents": [str(p) for p in self.graph.objects(c, RDFS.subClassOf)]}
                            for c in sorted(self.classes)],
                "relations": [{**item(p), "domain": [str(v) for v in self.constraint_types(p,RDFS.domain)],
                               "range": [str(v) for v in self.constraint_types(p,RDFS.range)]}
                              for p in sorted(self.relations)],
                "attributes": [{**item(p), "domain": [str(v) for v in self.constraint_types(p,RDFS.domain)],
                                "range": [str(v) for v in self.graph.objects(p, RDFS.range)]}
                               for p in sorted(self.attributes)], "triples": len(self.graph)}

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

    def dataset(self, records):
        graph = Graph()
        for prefix, ns in self.graph.namespaces():
            graph.bind(prefix, ns)
        graph += self.graph
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
                for v in value if isinstance(value, list) else [value]:
                    if isinstance(v, (str, int, float, bool)):
                        graph.add((node, predicate, Literal(v)))
        ids = {r['id'] for r in records if r['kind'] == 'entity'}
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
        from .time import normalize_time, utc_now
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
