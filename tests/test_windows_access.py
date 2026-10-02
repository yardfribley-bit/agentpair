import unittest
from agentpair.windows_access import bootstrap_script


class WindowsAccessTests(unittest.TestCase):
    def test_bootstrap_has_explicit_readiness_gate(self):
        script = bootstrap_script('ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest navigator@test')
        self.assertIn('OpenSSH.Server', script)
        self.assertIn('administrators_authorized_keys', script)
        self.assertIn('ssh_listening_authentication_pending', script)
        self.assertNotIn('password', script.lower())

    def test_rejects_key_injection(self):
        with self.assertRaises(ValueError):
            bootstrap_script("ssh-ed25519 AAAA';Invoke-Expression evil")
