import unittest


class IdentityKeyUnionTests(unittest.TestCase):
    def test_accepted_registered_keys_are_retained_and_conflicting_keys_reject(self):
        from knowledge_service.domain.entity_identity import IdentityPolicy,merge_identity_keys
        policy=IdentityPolicy('reference',True,keys=('code','tax'))
        left={'metadata':{'identity_keys':{'code':'A'}}}
        right={'metadata':{'identity_keys':{'tax':'B','unregistered':'ignored'}}}
        self.assertEqual(merge_identity_keys(left,right,policy),{'code':'A','tax':'B'})
        with self.assertRaisesRegex(ValueError,'身份键|唯一键'):
            merge_identity_keys(left,{'metadata':{'identity_keys':{'code':'C'}}},policy)


if __name__=='__main__':unittest.main()
