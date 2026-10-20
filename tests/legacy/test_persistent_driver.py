import unittest

from agentpair.transport import CloudDriverBackend


class NeverAllocate:
    def __getattr__(self, name):
        raise AssertionError('Persistent Driver should not use cloud allocation: '+name)


class PersistentDriverTests(unittest.TestCase):
    def test_registered_driver_selected_without_new_lease(self):
        backend=CloudDriverBackend('test',NeverAllocate(),'/tmp/key','/tmp/known','public',
                                   'firewall','/tmp',persistent_driver='165.154.254.180')
        self.assertEqual(backend._provision(),('165.154.254.180',True))
        self.assertEqual(backend.lease_id,'persistent:165.154.254.180')

    def test_second_parallel_branch_cannot_reuse_same_driver(self):
        backend=CloudDriverBackend('test',NeverAllocate(),'/tmp/key','/tmp/known','public',
                                   'firewall','/tmp',persistent_driver='165.154.254.180')
        with self.assertRaisesRegex(AssertionError,'cloud allocation'):
            backend._provision(excluded=['persistent:165.154.254.180'])


if __name__=='__main__': unittest.main()
