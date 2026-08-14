import json
import os
import unittest
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError

from regression_lab.openai_compatible import ModelClientError, OpenAICompatibleClient
from examples.external_openai_agent import NEGATIVE_CONTROL_REDUNDANT_COMPLETIONS, NEGATIVE_CONTROL_TOOL_CALL_RESERVE, _same_test_command, describe_protocol, execute, execute_with_outcome, is_negative_control, profile, should_force_negative_terminal, should_stop_after_tool


class OpenAICompatibleClientTests(unittest.TestCase):
    def test_requires_credentials_without_exposing_them(self):
        with self.assertRaises(ModelClientError) as captured:
            OpenAICompatibleClient(api_key="secret", model="")
        self.assertNotIn("secret", str(captured.exception))

    def test_parses_tool_call_and_usage(self):
        client = OpenAICompatibleClient(api_key="secret", model="example")
        payload = {"choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [{"id": "call_1", "function": {"name": "read_file", "arguments": '{\"path\":\"src/a.py\"}'}}]}}], "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}
        class Response:
            def read(self): return __import__("json").dumps(payload).encode()
            def __enter__(self): return self
            def __exit__(self, *args): return False
        with patch("regression_lab.openai_compatible.urlopen", return_value=Response()):
            reply = client.complete([{"role": "user", "content": "x"}], [], 100)
        self.assertEqual(reply.tool_calls[0].name, "read_file")
        self.assertEqual(reply.usage["total_tokens"], 5)

    def test_request_sends_the_frozen_sampling_configuration(self):
        client = OpenAICompatibleClient(
            api_key="secret", model="example", temperature=0.25, top_p=0.8, seed=7,
        )
        payload = {"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]}
        class Response:
            def read(self): return json.dumps(payload).encode()
            def __enter__(self): return self
            def __exit__(self, *args): return False
        with patch("regression_lab.openai_compatible.urlopen", return_value=Response()) as request:
            client.complete([{"role": "user", "content": "x"}], [], 100)
        body = json.loads(request.call_args.args[0].data.decode())
        self.assertEqual(body["temperature"], 0.25)
        self.assertEqual(body["top_p"], 0.8)
        self.assertEqual(body["seed"], 7)

    def test_environment_defaults_are_explicit(self):
        with patch.dict(os.environ, {"AGENT_API_KEY": "secret", "AGENT_MODEL": "example"}, clear=True):
            client = OpenAICompatibleClient.from_environment()
        self.assertEqual(client.temperature, 0.0)
        self.assertEqual(client.top_p, 1.0)
        self.assertIsNone(client.seed)

    def test_http_429_has_a_safe_quota_or_rate_limit_category(self):
        client = OpenAICompatibleClient(api_key="secret", model="example")
        with patch("regression_lab.openai_compatible.urlopen", side_effect=HTTPError("https://model.invalid", 429, "too many requests", {}, None)):
            with self.assertRaises(ModelClientError) as captured:
                client.complete([{"role": "user", "content": "x"}], [], 100)
        self.assertEqual(captured.exception.kind, "http_429")
        self.assertNotIn("secret", str(captured.exception))

    def test_bash_only_permits_the_platform_test_command(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(PermissionError):
                execute(root, "bash", {"command": "echo unsafe"}, ["**"], [], "python -m unittest")

    def test_test_command_allows_only_python_launcher_aliases(self):
        self.assertTrue(_same_test_command("python3 -m unittest discover -s tests -v", "python -m unittest discover -s tests -v"))
        self.assertFalse(_same_test_command("python -m unittest", "python -m unittest discover -s tests -v"))

    def test_v3_has_a_distinct_targeted_profile(self):
        profile_id, prompt = profile("external-openai-v3", "python -m unittest")
        self.assertEqual(profile_id, "targeted-context-verify-v3")
        self.assertIn("shortest correct tool path", prompt)
        self.assertNotEqual(profile_id, profile("external-openai-v2", "python -m unittest")[0])

    def test_v4_adds_only_success_stop_and_denied_path_guardrails(self):
        command = "python -m unittest"
        v3_id, v3_prompt = profile("external-openai-v3", command)
        v4_id, v4_prompt = profile("external-openai-v4", command)

        self.assertEqual(v3_id, "targeted-context-verify-v3")
        self.assertEqual(v4_id, "success-stop-verify-v4")
        self.assertTrue(v4_prompt.startswith(v3_prompt))
        self.assertIn("stop immediately", v4_prompt)
        self.assertIn("do not retry the same operation", v4_prompt)
        self.assertIn("__pycache__", v4_prompt)
        self.assertIn(".pyc", v4_prompt)

    def test_v4_1_stops_deterministically_after_successful_verification(self):
        command = "python -m unittest"
        v3_id, v3_prompt = profile("external-openai-v3", command)
        v4_1_id, v4_1_prompt = profile("external-openai-v4.1", command)
        self.assertEqual(v4_1_id, "bounded-success-stop-verify-v4-1")
        self.assertTrue(v4_1_prompt.startswith(v3_prompt))
        self.assertIn("platform will end the run", v4_1_prompt)
        self.assertTrue(should_stop_after_tool("external-openai-v4.1", "bash", True))
        self.assertFalse(should_stop_after_tool("external-openai-v4.1", "bash", False))
        self.assertFalse(should_stop_after_tool("external-openai-v4", "bash", True))
        self.assertFalse(should_stop_after_tool("external-openai-v4.1", "read_file", True))

    def test_negative_control_keeps_the_v3_prompt_and_declares_two_runtime_calls(self):
        profile_id, prompt = profile("external-openai-v3-negative", "python -m unittest")
        _, v3_prompt = profile("external-openai-v3", "python -m unittest")
        self.assertEqual(profile_id, "targeted-context-verify-v3-plus-two-redundant-completions")
        self.assertEqual(prompt, v3_prompt)
        self.assertEqual(NEGATIVE_CONTROL_REDUNDANT_COMPLETIONS, 2)
        self.assertEqual(NEGATIVE_CONTROL_TOOL_CALL_RESERVE, 2)
        self.assertTrue(is_negative_control("external-openai-v3-negative"))
        self.assertFalse(is_negative_control("external-openai-v4.1"))

    def test_negative_control_reserves_tool_budget_for_its_two_redundant_calls(self):
        self.assertTrue(should_force_negative_terminal("external-openai-v3-negative", 16, 18))
        self.assertTrue(should_force_negative_terminal("external-openai-v3-negative", 18, 18))
        self.assertFalse(should_force_negative_terminal("external-openai-v3-negative", 0, 18))
        self.assertFalse(should_force_negative_terminal("external-openai-v3", 16, 18))

    def test_negative_control_and_v3_have_the_same_rendered_prompt_hash(self):
        profiles = describe_protocol({
            "versions": ["external-openai-v3", "external-openai-v3-negative"],
            "test_commands": ["python -m unittest", "python -m pytest"],
        })["profiles"]
        self.assertEqual(
            profiles["external-openai-v3"]["rendered_prompt_set_hash"],
            profiles["external-openai-v3-negative"]["rendered_prompt_set_hash"],
        )

    def test_verification_outcome_uses_process_exit_code(self):
        with TemporaryDirectory() as directory:
            output, passed = execute_with_outcome(
                Path(directory), "bash", {"command": "python -c 'import sys; sys.exit(0)'"}, ["**"], [],
                "python -c 'import sys; sys.exit(0)'",
            )
        self.assertTrue(passed)
        self.assertEqual(output, "exit_code=0")

    def test_v2_and_v3_prompts_remain_frozen(self):
        command = "python -m unittest"
        base = "You are a coding agent. Work only in the supplied worktree. Use the available tools to inspect and repair the task. Do not modify tests."
        self.assertEqual(
            profile("external-openai-v2", command),
            (
                "observe-plan-act-verify-v2",
                base + f" First inspect the failing test and related source, plan one minimal change, then verify once using the bash tool with exactly this command: {command}. Do not repeat reads or verification unless an error occurs.",
            ),
        )
        self.assertEqual(
            profile("external-openai-v3", command),
            (
                "targeted-context-verify-v3",
                base + f" Start by locating the smallest relevant source and test context with one glob/read pass. Make one minimal edit, then verify once with the bash tool using exactly this command: {command}. Prefer the shortest correct tool path; only investigate further after a concrete error.",
            ),
        )

    def test_v3_and_v4_rendered_prompt_hashes_are_distinct(self):
        description = describe_protocol({
            "versions": ["external-openai-v3", "external-openai-v4"],
            "test_commands": ["python -m unittest", "python -m pytest"],
        })
        profiles = description["profiles"]
        self.assertEqual(profiles["external-openai-v4"]["profile_id"], "success-stop-verify-v4")
        self.assertNotEqual(
            profiles["external-openai-v3"]["rendered_prompt_set_hash"],
            profiles["external-openai-v4"]["rendered_prompt_set_hash"],
        )
