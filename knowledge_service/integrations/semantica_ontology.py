"""Stable boundary around Semantica's ontology quality gate."""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as package_version


def ontology_payload(ontology):
    """Convert the internal RDF ontology to Semantica's plain input schema."""
    from rdflib import RDF, RDFS
    from rdflib.namespace import OWL
    classes = []
    for term in sorted(ontology.classes, key=str):
        parents = sorted(str(value) for value in ontology.graph.objects(
            term, RDFS.subClassOf) if value in ontology.classes)
        classes.append({'uri': str(term), 'name': str(term), 'parents': parents})
    properties = []
    for term in sorted(ontology.relations | ontology.attributes, key=str):
        is_data = term in ontology.attributes or (
            term, RDF.type, OWL.DatatypeProperty) in ontology.graph
        properties.append({
            'uri': str(term), 'name': str(term),
            'type': 'data_property' if is_data else 'object_property',
            'domain': sorted(str(value) for value in ontology.graph.objects(term, RDFS.domain)),
            'range': sorted(str(value) for value in ontology.graph.objects(term, RDFS.range)),
        })
    return {'classes': classes, 'properties': properties}


class QualityEngineUnavailable(RuntimeError):
    code = 'quality_engine_unavailable'


class SemanticaOntologyQuality:
    def __init__(self, checker=None, version=None):
        if checker is None:
            try:
                from semantica.ontology import ontology_quality_check
            except Exception as exc:
                raise QualityEngineUnavailable(
                    'Semantica 本体质量引擎不可用') from exc
            checker = ontology_quality_check
        self.checker = checker
        if version is None:
            try:
                version = package_version('semantica')
            except PackageNotFoundError:
                version = 'unavailable'
        self.version = str(version)

    def check(self, ontology, *, thresholds=None, competency_questions=None):
        questions = [str(item) for item in (competency_questions or [])]
        try:
            raw = self.checker(
                ontology,
                thresholds=thresholds,
                competency_questions=questions,
            )
            data = raw.to_dict()
        except Exception as exc:
            raise QualityEngineUnavailable(
                'Semantica 本体质量检查执行失败') from exc
        issues = []
        for item in data.get('issues') or []:
            issues.append({
                'code': str(item.get('code') or 'unknown').lower(),
                'message': str(item.get('message') or ''),
                'severity': str(item.get('severity') or 'error').lower(),
                'element_id': item.get('element_id'),
                'element_type': item.get('element_type'),
                'details': dict(item.get('details') or {}),
            })
        if not data.get('passed') and not any(
                issue['severity'] in {'error', 'critical'} for issue in issues):
            # Some upstream reports fail only a metric threshold and contain
            # warnings, not error issues. Preserve passed=False as a real gate.
            issues.append({
                'code': 'quality_gate_failed', 'severity': 'error',
                'message': 'Semantica 本体质量门禁未通过',
                'element_id': None, 'element_type': None,
                'details': {'threshold_failures': list(data.get('threshold_failures') or [])},
            })
        return {
            'engine': 'semantica',
            'engine_version': self.version,
            'passed': bool(data.get('passed')),
            'issues': issues,
            'stats': dict(data.get('stats') or {}),
            'metrics': dict(data.get('metrics') or {}),
            'thresholds': dict(data.get('thresholds') or {}),
            'threshold_failures': list(data.get('threshold_failures') or []),
            'competency_questions': questions,
        }
