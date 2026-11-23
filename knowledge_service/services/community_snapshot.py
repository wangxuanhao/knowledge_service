"""Assemble browseable communities and frozen references from one graph snapshot."""
from collections import defaultdict
from hashlib import sha256
import json

from .community_partition import partition
from .evidence import frozen_assertion_source


def digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return sha256(encoded.encode()).hexdigest()


def capture_evidence(repository, project, records, supports):
    connected = {row[field] for row in records if row['kind'] == 'relation'
                 for field in ('id', 'subject_id', 'object_id')}
    records = {row['id']: row for row in records if row['id'] in connected}
    evidence = defaultdict(list)
    for support in supports:
        source = frozen_assertion_source(repository, project, support['assertion_id'])
        if not source or source['document_version_id'] != support['document_version_id']:
            continue
        record = records[support['record_id']]
        evidence[record['id']].append({
            'id': 'E-' + digest([record['version_id'], support['id']])[:24],
            'kind': 'document', 'record_id': record['id'], 'record_kind': record['kind'],
            'record_version_id': record['version_id'],
            'supported_record_version_id': support['record_version_id'],
            'assertion_event_id': support['id'], **source,
        })
    for record in records.values():
        if not evidence[record['id']]:
            evidence[record['id']].append({
                'id': 'E-' + digest(record['version_id'])[:24],
                'kind': 'record', 'record_id': record['id'],
                'record_kind': record['kind'], 'record_version_id': record['version_id'],
                'assertion_id': None, 'document_id': None, 'document_version_id': None,
                'source_version_id': None, 'source_location': None, 'chunk_id': None,
                'chunk_version_id': None, 'start_char': None, 'end_char': None,
                'title': record['text'] or record['id'], 'quote': record['text'] or record['id'],
            })
    return evidence


def assemble(project, scope_key, records, evidence, prior_reports):
    entities = {record['id']: record for record in records if record['kind'] == 'entity'}
    relations = [record for record in records if record['kind'] == 'relation']
    endpoints = [(record['subject_id'], record['object_id']) for record in relations]
    communities, memberships, degrees = {}, defaultdict(list), defaultdict(int)
    for level, members in partition(entities, endpoints):
        ident = 'community-' + digest([project, scope_key, members])[:32]
        communities[ident] = {
            'id': ident, 'level': level, 'member_ids': list(members), 'parent_id': None,
            'content': {'members': [], 'relationships': [], 'evidence': []}}
        for entity in members:
            memberships[entity].append(ident)
            communities[ident]['content']['members'].append(entities[entity])
    for relation in relations:
        subject, target = relation['subject_id'], relation['object_id']
        degrees[subject] += 1
        degrees[target] += 1
        source_groups, target_groups = set(memberships[subject]), set(memberships[target])
        shared = source_groups & target_groups
        for ident in source_groups | target_groups:
            communities[ident]['content']['relationships'].append({
                **relation, 'subject_text': entities[subject]['text'], 'object_text': entities[target]['text'],
                'boundary': ident not in shared})
    for community in communities.values():
        content, members = community['content'], community['member_ids']
        parent = [ident for ident in memberships[members[0]]
                  if communities[ident]['level'] < community['level']]
        if parent:
            community['parent_id'] = parent[-1]
        labels = [entities[ident]['text'] or ident for ident in
                  sorted(members, key=lambda ident: (-degrees[ident], ident))[:3]]
        community['title'] = ' · '.join(labels)[:180]
        relations = sorted(content['relationships'], key=lambda item: item['id'])
        content['relationships'] = relations
        source_ids = [item['id'] for item in relations] + members
        references = [ref for ident in source_ids for ref in evidence[ident]]
        content['evidence'] = sorted(references, key=lambda ref: ref['kind'] != 'document')
        content['digest'] = digest(content)
        community['report'] = prior_reports.get((community['id'], content['digest']),
                                                {'status': 'pending'})
        # Store a compact searchable representation without loading it in list responses.
        search_text = [community['title']]
        for item in [*content['members'], *relations]:
            search_text.extend(str(item.get(key) or '') for key in ('id', 'text', 'type', 'aliases'))
        search_text.extend(ref['quote'] for ref in references)
        community['search_text'] = '\n'.join(search_text).casefold()
    return list(communities.values()), {
        'communities': len(communities), 'entities': len(entities),
        'covered_entities': len(memberships),
        'isolated_entities': len(entities) - len(memberships),
        'relationships': len(endpoints), 'evidence': sum(map(len, evidence.values()))}
