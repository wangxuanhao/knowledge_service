"""Policy capabilities belong to canonical entities, never inherited fact metadata."""
from knowledge_service.domain.entity_identity import (IdentityPolicy, policy_source_qualified,
                                                       source_qualified)


def test_source_qualification_never_applies_to_attribute_or_relation():
    policy = IdentityPolicy(mode='reference', declared=True)
    for kind in ('attribute', 'relation', 'document', 'chunk'):
        row = {'kind': kind, 'metadata': {'identity_policy': 'reference',
                                         'identity_source_qualified': True}}
        assert not source_qualified(row)
        assert not policy_source_qualified(row, policy)
