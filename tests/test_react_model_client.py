import unittest

from adapters.react_agent.model_client import ModelClientError, parse_chat_completion


class ReactModelClientTests(unittest.TestCase):
    def test_parses_function_call_and_usage_without_provider_sdk(self):
        reply = parse_chat_completion({
            "choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [{"id": "call_1", "function": {"name": "read_file", "arguments": '{"path":"src/app.py"}'}}]}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })

        self.assertEqual(reply.tool_calls[0].name, "read_file")
        self.assertEqual(reply.tool_calls[0].arguments, {"path": "src/app.py"})
        self.assertEqual(reply.usage["total_tokens"], 15)

    def test_invalid_tool_arguments_fail_closed(self):
        with self.assertRaises(ModelClientError):
            parse_chat_completion({
                "choices": [{"message": {"tool_calls": [{"id": "call_1", "function": {"name": "read_file", "arguments": "not-json"}}]}}],
            })
