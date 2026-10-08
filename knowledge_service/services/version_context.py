"""Read-only knowledge context for one immutable ontology version."""

from .reclassify import Reclassify


KNOWLEDGE_KINDS = ('entity', 'relation', 'attribute')


class OntologyVersionContext:
    def __init__(self, service):
        self.service = service
        self.repository = service.repository

    def get(self, project_id, ontology_id):
        with self.repository.read_snapshot():
            versions = self.repository.list_ontologies(project_id)
            target = next(
                (version for version in versions if version['id'] == ontology_id),
                None,
            )
            if target is None:
                raise KeyError(ontology_id)
            current = versions[-1]
            is_current = current['id'] == target['id']

            all_current = self.service.scoped(project_id, {
                'kinds': list(KNOWLEDGE_KINDS),
                'ontology_scope': 'all',
                'ontology_ids': None,
            })
            bound = self.service.scoped(project_id, {
                'kinds': list(KNOWLEDGE_KINDS),
                'ontology_scope': 'ids',
                'ontology_ids': [target['id']],
            })
            by_kind = {kind: 0 for kind in KNOWLEDGE_KINDS}
            for row in bound:
                by_kind[row['kind']] += 1

            if is_current:
                pending = blocked = 0
            else:
                try:
                    plan = Reclassify(self.service).plan(project_id)
                except Exception:
                    pending = blocked = None
                else:
                    groups = [
                        group for group in plan.get('groups', ())
                        if group.get('from_ontology_id') == target['id']
                    ]
                    pending = sum(
                        group.get('record_count', 0)
                        for group in groups if group.get('migratable') is True
                    )
                    blocked = sum(
                        group.get('record_count', 0)
                        for group in groups if group.get('migratable') is False
                    )

            history = self.repository.ontology_record_history(
                project_id, target['id'], all_current)
            return {
                'ontology': {
                    'id': target['id'],
                    'version': target['version'],
                    'is_current': is_current,
                    'created_at': target['created_at'],
                },
                'current_bound': {'total': len(bound), 'by_kind': by_kind},
                'migration': {'pending': pending, 'blocked': blocked},
                'history': history,
            }
