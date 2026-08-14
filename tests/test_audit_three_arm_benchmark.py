import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.audit_three_arm_benchmark import AuditError, audit


class ThreeArmAuditTests(unittest.TestCase):
    def test_refuses_missing_formal_artifacts_without_mutating_them(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(AuditError, "experiment"):
                audit(root)
            self.assertEqual(list(root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
