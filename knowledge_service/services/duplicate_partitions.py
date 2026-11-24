"""Review all members of a duplicate candidate group in one decision."""
from dataclasses import dataclass
import hashlib
import json
from uuid import uuid4

from ..core.time import utc_now
from ..domain.entity_identity import (compatible_identity, identity_scope, key_conflict,
                                      merge_identity_keys, require_projection_ready, source_boundary)
from ..domain.formal_facts import canonical_attribute_key, canonical_relation_key
from ..repository.attribute_conflicts import ConflictReads
from ..repository.duplicate_entities import DocumentScope, DuplicateReads, DuplicateReviewRequired
from .formal_writes import _published_identity_policy
from .governance import writable
from .ontology import Ontology


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def survivor_identity(repo, project, keep, drops, policy):
    metadata = dict(keep.get('metadata') or {})
    aliases = list(metadata.get('aliases', []))
    for member in drops:
        accumulated = {**keep, 'metadata': metadata}
        if not compatible_identity(accumulated, member, policy):
            if keep['type'] != member['type'] or identity_scope(keep, policy) != identity_scope(member, policy):
                differences = ['身份范围']
            elif key_conflict(accumulated, member, policy):
                differences = ['唯一键']
            else:
                left, right = source_boundary(keep), source_boundary(member)
                differences = [label for label, differs in (
                    ('业务有效期', left[:2] != right[:2]), ('适用范围', left[2] != right[2])) if differs]
            raise DuplicateReviewRequired(f"成员 {member['id']} 与保留实体 {keep['id']} 的{'、'.join(differences)}不同，"
                                          "请选择其他保留实体或保持独立。")
        if source_boundary(keep) != source_boundary(member):
            try:
                require_projection_ready(repo, project)
            except ValueError as exc:
                raise DuplicateReviewRequired(str(exc)) from exc
        metadata['identity_keys'] = merge_identity_keys(accumulated, member, policy)
        aliases.extend([member['text'], *(member.get('metadata') or {}).get('aliases', [])])
    return {**metadata, 'aliases': list(dict.fromkeys(aliases)), 'merge_backend': 'confirmed_identity_group'}


def affected_counts(rows, assignments, targets):
    """Count facts moved, quarantined or coalesced for each proposed survivor."""
    facts = {}
    touched = {t['keep_id']: {'relations': set(), 'attributes': set()} for t in targets}
    for target in targets:
        for conflict in target['attribute_conflicts']:
            touched[target['keep_id']]['attributes'].update(conflict['record_ids'])
    for row in rows.values():
        if row['kind'] not in ('attribute', 'relation') or row.get('metadata', {}).get('_quarantined'):
            continue
        attribute = row['kind'] == 'attribute'
        fields = ('subject_id',) if attribute else ('subject_id', 'object_id')
        endpoints = {f: assignments.get(row[f], row[f]) for f in fields}
        affected = {endpoints[f] for f in fields if endpoints[f] != row[f]}
        changed = {**row, **endpoints}
        fact_key = canonical_attribute_key(changed) if attribute else canonical_relation_key(changed)
        bucket = 'attributes' if attribute else 'relations'
        facts.setdefault((bucket, fact_key), []).append((row['id'], affected))
    for (bucket, _), group in facts.items():
        for survivor in set().union(*(affected for _, affected in group)):
            touched[survivor][bucket].update(record_id for record_id, _ in group)
    return {survivor: {kind: len(ids) for kind, ids in buckets.items()} for survivor, buckets in touched.items()}


@dataclass
class PartitionPlan:
    members: dict
    rows: dict
    targets: list
    identity_metadata: dict
    fingerprint: str
    scope: DocumentScope | None

    def preview(self):
        return dict(preview_fingerprint=self.fingerprint, targets=self.targets)


def commit_merges(governance, project, key, fingerprint, targets, rows, members, identity_metadata, actor,
                  *, defer_sync=False):
    """All endpoint rewrites, assertion moves and per-owner conflict sets use the same formal ledger."""
    updates, redirects, actions, quarantine = {}, {}, [], []
    conflicts_by_record = {}
    for target in targets:
        keep_id = target['keep_id']
        if not target['drop_ids']:
            continue
        updates[keep_id] = {**writable(members[keep_id]), 'metadata': identity_metadata[keep_id]}
        for drop_id in target['drop_ids']:
            drop = members[drop_id]
            redirects[drop_id] = keep_id
            updates[drop_id] = {**writable(drop), 'metadata': {**drop.get('metadata', {}), '_deleted': True,
                                                               'merged_into': keep_id}}
        by_predicate = {}
        for conflict in target['attribute_conflicts']:
            by_predicate.setdefault(conflict['predicate'], set()).update(conflict['record_ids'])
        for predicate, ids in sorted(by_predicate.items()):
            set_id = 'conflict:' + str(uuid4())
            actions.append(dict(kind='conflict_set_create', id=set_id, entity_id=keep_id,
                                predicate=predicate, member_ids=sorted(ids)))
            conflicts_by_record.update(dict.fromkeys(ids, set_id))
            quarantine.append(dict(entity_id=keep_id, predicates=[predicate], member_ids=sorted(ids),
                                   preview_fingerprint=fingerprint))

    for row in sorted(rows.values(), key=lambda r: r['id']):
        if row['kind'] not in {'relation', 'attribute'}:
            continue
        fields = ('subject_id', 'object_id') if row['kind'] == 'relation' else ('subject_id',)
        endpoints = {field: redirects[row[field]] for field in fields if row[field] in redirects}
        set_id = conflicts_by_record.get(row['id'])
        if endpoints or set_id:
            updated = {**writable(row), **endpoints}
            if set_id:
                updated['metadata'] = {**row.get('metadata', {}), '_quarantined': True, 'conflict_set_ids': [set_id]}
            updates[row['id']] = updated
    decisions = [dict(id=a['id'], expected_version=a['decision_version'], status='contradicting',
                      canonical_record_id=a['canonical_record_id'], reason='确认身份融合；单值属性隔离，等待集合审核', actor=actor)
                 for a in ConflictReads(governance.repo).support_versions(project, list(conflicts_by_record))
                 if a['status'] == 'accepted']
    result = governance._commit(project, [rows[record_id] for record_id in updates], list(updates.values()), 'merge',
        'confirmed_identity_group', redirects=redirects, assertion_decisions=decisions, conflict_actions=actions,
        expected_dependencies={r['id']: r['version'] for r in rows.values()},
        audit_extra=dict(actor=actor, preview_fingerprint=fingerprint, duplicate_group=key),
        quarantine_conflicts=quarantine or None, defer_milvus_sync=defer_sync)
    return result, actions


class DuplicatePartitions:
    def __init__(self, governance):
        self.governance = governance
        self.repo = governance.repo
        self.reads = DuplicateReads(self.repo)

    def plan(self, project, key, assignments, expected_versions):
        group, scope = self.reads.group_context(project, key)
        members = {row['id']: row for row in group}
        if set(assignments) != members.keys():
            missing = sorted(members.keys() - assignments.keys())
            extra = sorted(assignments.keys() - members.keys())
            reasons = []
            if missing:
                reasons.append('未分配成员 ' + '、'.join(missing))
            if extra:
                reasons.append('不属于本组的成员 ' + '、'.join(extra))
            raise ValueError('版本冲突：必须分配当前组的全部成员。' + '；'.join(reasons) + '，请重新扫描。')
        if expected_versions != {r['id']: r['version'] for r in group}:
            raise ValueError('版本冲突：请重新读取全部成员的当前版本并复核分配。')
        destinations = {}
        for member, keep in sorted(assignments.items()):
            if keep not in members:
                raise ValueError(f'成员 {member} 指定的目标 {keep} 不属于当前重复组，请选择组内实体。')
            if assignments[keep] != keep:
                raise ValueError(f'成员 {member} 的目标 {keep} 也被分配给其他实体，请将目标设为保留实体。')
            drops = destinations.setdefault(keep, [])
            if member != keep:
                drops.append(member)
        ids = sorted(members)
        rows = members
        targets, metadata = [], {}
        if len(destinations) < len(members):
            participants = [member for member, keep in assignments.items() if destinations[keep]]
            rows = self.reads.merge_rows(project, participants)
            policy = _published_identity_policy(Ontology(self.repo.get_ontology(project)['turtle']), group[0]['type'])
        for keep_id, drops in sorted(destinations.items()):
            conflicts = []
            if drops:
                keep = members[keep_id]
                metadata[keep_id] = survivor_identity(self.repo, project, keep, [members[drop] for drop in drops], policy)
                owners = {keep_id, *drops}
                attrs = sorted((r for r in rows.values() if r['kind'] == 'attribute' and r['subject_id'] in owners),
                               key=lambda r: r['id'])
                if any(r.get('metadata', {}).get('_quarantined') for r in attrs):
                    raise DuplicateReviewRequired(f'保留实体 {keep_id} 或其待合并成员已有隔离属性，请先审核冲突集合或保持独立。')
                conflicts = self.governance._attribute_conflicts(project, keep, attrs)
            targets.append(dict(keep_id=keep_id, drop_ids=drops, attribute_conflicts=conflicts))
        counts = affected_counts(rows, assignments, targets)
        for target in targets:
            target['affected_records'] = counts[target['keep_id']]
        signature = self.reads.review_signature(project, ids, scope)
        prior = self.reads.current_review_receipt(project, key, group, scope, signature=signature)
        fingerprint = digest([project, key, assignments, expected_versions, signature, targets,
                              prior['preview_fingerprint'] if prior else None])
        return PartitionPlan(members, rows, targets, metadata, fingerprint, scope)

    def _result(self, project, receipt):
        records = []
        if receipt.get('operation_id'):
            snapshots = self.repo.merge_operation_snapshots(project, receipt['operation_id'])
            records = [r for r in snapshots['after_state'] if not r.get('metadata', {}).get('_audit')]
        return {**receipt, 'records': records}

    def confirm(self, project, key, assignments, expected_versions, fingerprint, actor):
        request_hash = digest([project, key, assignments, expected_versions])
        with self.reads.review_transaction():
            prior = self.reads.confirmed_receipt(project, fingerprint)
            if prior:
                if request_hash != prior['request_hash']:
                    raise ValueError('版本冲突：此预览对应另一份分配，请重新预览。')
                return self._result(project, prior['receipt'])
            plan = self.plan(project, key, assignments, expected_versions)
            if plan.fingerprint != fingerprint:
                raise ValueError('版本冲突：组内成员、证据、规则或影响记录已变化，请重新预览。')
            receipt = dict(key=key, status='completed', actor=actor, reviewed_at=utc_now(), preview_fingerprint=fingerprint,
                assigned_count=len(assignments), kept_count=len(plan.targets), merged_count=len(assignments) - len(plan.targets),
                targets=[{'keep_id': t['keep_id'], 'drop_ids': t['drop_ids']} for t in plan.targets])
            if receipt['merged_count']:
                result, actions = commit_merges(self.governance, project, key, fingerprint, plan.targets, plan.rows,
                    plan.members, plan.identity_metadata, actor, defer_sync=True)
                receipt.update(operation_id=result['operation_id'], conflict_set_ids=[action['id'] for action in actions])
            self.reads.save_review(project, key, receipt, request_hash, plan.scope)
            response = self._result(project, receipt)
        if receipt['merged_count']:
            self.governance.service._drain_projections(project)
        return response
