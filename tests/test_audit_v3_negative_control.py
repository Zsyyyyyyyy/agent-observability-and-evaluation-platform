import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.audit_v3_negative_control import AuditError, audit


class V3NegativeControlAuditTests(unittest.TestCase):
    def test_missing_artifacts_fail_without_writes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(AuditError, "experiment"):
                audit(root)
            self.assertEqual(list(root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
