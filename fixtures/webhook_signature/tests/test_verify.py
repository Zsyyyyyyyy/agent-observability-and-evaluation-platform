import unittest

from src.verify import signature_for, verify_signature


class WebhookSignatureTests(unittest.TestCase):
    def test_equivalent_mapping_order_produces_the_same_signature(self):
        first = {"event": "invoice.paid", "data": {"id": "inv-1", "amount": 42}}
        reordered = {"data": {"amount": 42, "id": "inv-1"}, "event": "invoice.paid"}
        signature = signature_for(first, "top-secret")
        self.assertTrue(verify_signature(reordered, signature, "top-secret"))

    def test_tampering_or_wrong_secret_fails_closed(self):
        event = {"event": "invoice.paid", "data": {"id": "inv-1", "amount": 42}}
        signature = signature_for(event, "top-secret")
        self.assertFalse(verify_signature({"event": "invoice.paid", "data": {"id": "inv-1", "amount": 43}}, signature, "top-secret"))
        self.assertFalse(verify_signature(event, signature, "other-secret"))

    def test_malformed_signature_is_not_accepted(self):
        event = {"event": "invoice.paid", "data": {"id": "inv-1"}}
        self.assertFalse(verify_signature(event, None, "top-secret"))
        self.assertFalse(verify_signature(event, "not-hex", "top-secret"))
