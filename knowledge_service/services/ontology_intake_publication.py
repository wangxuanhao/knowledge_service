"""Compose frozen source-effect plans without weakening the publish transaction."""
from copy import deepcopy

from .ontology import Ontology
from .ontology_drafts_core import StaleSource, ValidationFailed, _fingerprint
from .ontology_intake import intake_run_ids, materialization_context, prepare_intake_facts


def required_operation_ids(binding, history, effective, approved, turtle):
    """Resolve replacements through current, approved operation lineage.

    The resulting ontology still validates every candidate. A withdrawn/rejected
    creation cannot become accepted merely because a similarly named term exists.
    """
    return ApprovedDependencies(history, effective, approved, turtle).missing(binding)


class ApprovedDependencies:
    """One indexed final-operation snapshot for all candidates in a batch."""
    def __init__(self, history, effective, approved, turtle):
        self.by_id = {x['id']: x for x in history}
        self.descendants = {x.get('supersedes_operation_id'): x for x in history
                            if x.get('supersedes_operation_id')}
        self.active = {x['id']: x for x in effective}
        self.approved_ids = {x['id'] for x in approved}
        self.ontology = Ontology(turtle)

    def missing(self, binding):
        missing = []
        for original_id in binding.get('required_operation_ids') or []:
            current_id = original_id
            seen = set()
            while current_id in self.descendants and current_id not in seen:
                seen.add(current_id)
                current_id = self.descendants[current_id]['id']
            current = self.active.get(current_id)
            original = self.by_id.get(original_id)
            if (not current or not original or current_id not in self.approved_ids
                    or current['action'] != original['action']
                    or current['target_iri'] != binding['target_iri']):
                missing.append(original_id)
        if not self.ontology.is_active_term(binding['target_iri']):
            missing.extend(binding.get('required_operation_ids') or [])
        return list(dict.fromkeys(missing))


def composite_plan(drafts, project_id, draft, turtle, approved):
    context = draft.get('source_context') or {}
    entries = []
    direct_effect = context.get('publication_effects')
    if direct_effect:
        if (not isinstance(direct_effect, dict)
                or direct_effect.get('kind') not in {'candidate'}):
            raise StaleSource('obsolete source effects; select candidates through current intake')
        view = {**draft, 'source_context': {**context, 'intake_bindings': []}}
        plan = drafts._single_publication_effect_plan(project_id, view, turtle, approved)
        entries.append({'effects': direct_effect, 'source_kind': draft['source_kind'], 'plan': plan})
    for run_id in intake_run_ids(context):
        effects = {'kind': 'discovery', 'discovery_run_id': run_id}
        view = {**draft, 'source_kind': 'discovery',
                'source_context': {**context, 'publication_effects': effects, 'intake_bindings': []}}
        plan = drafts._single_publication_effect_plan(project_id, view, turtle, approved)
        entries.append({'effects': effects, 'source_kind': 'discovery', 'plan': plan})
    seen = {}
    batches = []
    for entry in entries:
        plan = entry['plan']
        if plan.get('kind') != 'discovery':
            continue
        unique = []
        for record in plan.get('records') or []:
            fingerprint = _fingerprint(record)
            if record['id'] in seen:
                if seen[record['id']] != fingerprint:
                    raise StaleSource('同一候选在多个来源绑定中产生不同事实，请核对映射')
                continue
            seen[record['id']] = fingerprint
            unique.append(record)
        plan['records'] = unique
        outcomes = plan.get('candidate_outcomes') or []
        accepted = plan.get('accepted_records') or unique
        batches.append({'run_id': plan.get('run_id'),
            'selected': len(outcomes), 'added': len(unique),
            'reused': max(0, len(accepted) - len(unique)),
            'skipped': sum(x.get('status') == 'skipped' for x in outcomes),
            'candidate_outcomes': deepcopy(outcomes)})
    records = [record for entry in entries for record in (
        entry['plan'].get('accepted_records') or entry['plan'].get('records') or [])
        if record.get('kind') in {'entity', 'relation', 'attribute'}]
    existing = drafts.repository.current_records(
        project_id, kinds=['entity', 'relation', 'attribute'])
    report = Ontology(turtle).validate_timeline(materialization_context(records, existing))
    if not report['conforms']:
        raise ValidationFailed(
            '关联事实与现有知识合并后未通过最终获批本体校验，请核对批次和冲突事实',
            details={'report': report})
    candidates = [candidate for run_id in intake_run_ids(context)
                  for candidate in drafts.repository.get_discovery_run(project_id, run_id)['candidate_snapshot']]
    formal = prepare_intake_facts(drafts.repository, project_id, records, candidates)
    created = {action['record']['id'] for action in formal['actions'] if action['kind'] == 'record_put'}
    counted = set()
    for batch in batches:
        entry = next(x for x in entries if x['plan'].get('run_id') == batch['run_id'])
        accepted = entry['plan'].get('accepted_records') or []
        canonical = {formal['canonical_by_input'].get(x['id'], x['id']) for x in accepted}
        added = (canonical & created) - counted
        batch['added'] = len(added)
        batch['reused'] = sum(x['status']=='materialized' for x in batch['candidate_outcomes']) - len(added)
        counted.update(added)
    facts = {key: sum(x[key] for x in batches)
             for key in ('selected', 'added', 'reused', 'skipped')}
    return {'kind': 'composite', 'entries': entries, 'formal_write_plan': formal,
            'receipt': {'facts': {**facts, 'per_batch': batches}}}
