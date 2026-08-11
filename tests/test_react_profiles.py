import unittest

from adapters.react_agent.worker import _agent_profile


class ReactProfileTests(unittest.TestCase):
    def test_v1_remains_the_basic_profile(self):
        profile, prompt = _agent_profile("react-agent-v1", "python -m unittest")
        self.assertEqual(profile, "react-basic-v1")
        self.assertNotIn("verify-once", prompt)

    def test_v2_exposes_the_exact_single_verification_command(self):
        profile, prompt = _agent_profile("react-agent-v2", "python -m unittest discover -s tests -v")
        self.assertEqual(profile, "verify-once-v2")
        self.assertIn("python -m unittest discover -s tests -v", prompt)
        self.assertIn("Do not repeat", prompt)
