"""Project-scoped RDF/OWL and SHACL interpretation; no network lookups."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, unquote

from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import OWL, SH

DATA = Namespace("urn:knowledge:")


def local_name(uri):
    value = str(uri).rsplit("#", 1)[-1].rsplit("/", 1)[-1]
    # Project ontologies use URNs (urn:knowledge:ontology:<project>:Term).
    # A URN has no slash/hash fragment, so the old implementation leaked the
    # entire storage identifier into every user-facing name and description.
    return value.rsplit(":", 1)[-1] if value.startswith("urn:") else value


def parse_shacl_focus_records(shacl_report_text: str, known_ids: set[str]) -> set[str]:
    """Extract record IDs from SHACL focus-node IRIs that match known records.

    Focus nodes look like <urn:knowledge:doc_id%3Achunk%3A0%3Aentity_xxx>
    where DATA = Namespace("urn:knowledge:") and the id was quote(r['id'], safe='').
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
    """Convert pySHACL result nodes into stable, UI-safe audit fields."""
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
            raise ValueError(f"Invalid Turtle: {exc}") from exc
        self.classes = set(self.graph.subjects(RDF.type, OWL.Class)) | set(self.graph.subjects(RDF.type, RDFS.Class))
        self.relations = set(self.graph.subjects(RDF.type, OWL.ObjectProperty))
        self.attributes = set(self.graph.subjects(RDF.type, OWL.DatatypeProperty))

    def resolve(self, name: str, allowed=None):
        allowed = allowed if allowed is not None else self.classes | self.relations | self.attributes
        matches = [uri for uri in allowed if str(uri) == name or local_name(uri) == name]
        if len(matches) != 1:
            raise ValueError(f"Unknown or ambiguous ontology term: {name}")
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
                "relations": [{**item(p), "domain": [str(v) for v in self.graph.objects(p, RDFS.domain)],
                               "range": [str(v) for v in self.graph.objects(p, RDFS.range)]}
                              for p in sorted(self.relations)],
                "attributes": [{**item(p), "domain": [str(v) for v in self.graph.objects(p, RDFS.domain)],
                                "range": [str(v) for v in self.graph.objects(p, RDFS.range)]}
                               for p in sorted(self.attributes)], "triples": len(self.graph)}

    def parents(self, term):
        seen, pending = {term}, [term]
        while pending:
            for parent in self.graph.objects(pending.pop(), RDFS.subClassOf):
                if parent not in seen:
                    seen.add(parent)
                    pending.append(parent)
        return seen

    def relation_constraint_issues(self,predicate,subject_type,object_type):
        """Return RDFS domain/range mismatches without treating OWL inference as validation."""
        predicate=self.resolve(predicate,self.relations)
        actual={'subject_id':self.resolve(subject_type,self.classes),'object_id':self.resolve(object_type,self.classes)}
        issues=[]
        for key,constraint in [('subject_id',RDFS.domain),('object_id',RDFS.range)]:
            expected=list(self.graph.objects(predicate,constraint))
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
                            raise ValueError(f"Missing relation endpoint: {r.get(key)}")
                        term = self.resolve(endpoint.get('type', ''), self.classes)
                        for expected in self.graph.objects(predicate, constraint):
                            if enforce_relationship_constraints and expected not in self.parents(term):
                                raise ValueError(f"{key} type {term} does not satisfy {constraint} {expected}")
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
        """SHACL sees only coexisting facts, once per interval where membership changes."""
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
        # Deny remote datasets/federation and updates, even if the query starts with PREFIX.
        if re.search(r'\b(SERVICE|FROM|LOAD|INSERT|DELETE|CLEAR|CREATE|DROP|MOVE|COPY|ADD)\b', query, re.I):
            raise ValueError("Only local SELECT/ASK queries are allowed")
        from rdflib.plugins.sparql.parser import parseQuery
        try:
            parsed = parseQuery(query)
            if parsed[1].name not in ('SelectQuery', 'AskQuery'):
                raise ValueError("Only SELECT/ASK supported")
            result = self.dataset(records).query(query)
        except Exception as exc:
            raise ValueError(f"Invalid local SPARQL: {exc}") from exc
        if result.type == 'ASK':
            return {"type": "ASK", "value": bool(result.askAnswer)}
        rows = []
        for row in result:
            if len(rows) >= 1000:
                break
            rows.append({str(k): str(v) if v is not None else None for k, v in zip(result.vars, row)})
        return {"type": "SELECT", "columns": [str(v) for v in result.vars], "rows": rows, "limit": 1000}
