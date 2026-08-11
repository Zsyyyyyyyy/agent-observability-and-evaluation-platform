import unittest

from regression_lab.adapters import AdapterError, get_adapter, registered_adapters


class AdapterRegistryTests(unittest.TestCase):
    def test_s20_replay_is_a_runnable_registered_adapter(self):
        adapter = get_adapter("s20-replay")

        self.assertTrue(adapter.worker_path.is_file())
        self.assertEqual(adapter.default_version, "s20-baseline-replay-v1")
        self.assertIn("edit_file", adapter.capabilities)
        self.assertEqual([item.adapter_id for item in registered_adapters()], ["failure-probe", "react-agent", "s20-replay"])

    def test_failure_probe_is_explicitly_registered_for_platform_self_tests(self):
        adapter = get_adapter("failure-probe")
        self.assertTrue(adapter.worker_path.is_file())
        self.assertEqual(adapter.default_version, "failure-probe-v1")

    def test_unknown_adapter_is_rejected(self):
        with self.assertRaises(AdapterError):
            get_adapter("not-an-adapter")


if __name__ == "__main__":
    unittest.main()
