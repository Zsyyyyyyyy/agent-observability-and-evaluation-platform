import unittest

from regression_lab.adapters import AdapterError, get_adapter, registered_adapters


class AdapterRegistryTests(unittest.TestCase):
    def test_readonly_replay_is_a_runnable_registered_adapter(self):
        adapter = get_adapter("readonly-replay")

        self.assertTrue(adapter.worker_path.is_file())
        self.assertEqual(adapter.default_version, "readonly-replay-v1")
        self.assertIn("edit_file", adapter.capabilities)
        self.assertTrue(adapter.evidence_capabilities.context_trace)
        self.assertEqual([item.adapter_id for item in registered_adapters()], ["external-command", "failure-probe", "react-agent", "readonly-replay"])

    def test_external_command_is_registered_with_its_own_worker(self):
        adapter = get_adapter("external-command")
        self.assertTrue(adapter.worker_path.is_file())
        self.assertEqual(adapter.default_version, "external-agent-v1")
        self.assertTrue(adapter.evidence_capabilities.hierarchical_trace)
        self.assertFalse(adapter.evidence_capabilities.test_trace)

    def test_failure_probe_is_explicitly_registered_for_platform_self_tests(self):
        adapter = get_adapter("failure-probe")
        self.assertTrue(adapter.worker_path.is_file())
        self.assertEqual(adapter.default_version, "failure-probe-v1")

    def test_unknown_adapter_is_rejected(self):
        with self.assertRaises(AdapterError):
            get_adapter("not-an-adapter")


if __name__ == "__main__":
    unittest.main()
