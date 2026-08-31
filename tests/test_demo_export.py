import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from regression_lab.dashboard import DashboardRepository
from scripts.export_demo_runtime import export_demo, verify_demo


class DemoExportTests(unittest.TestCase):
    def test_bundled_demos_keep_the_public_console_contract(self):
        project_root = Path(__file__).resolve().parents[1]
        instrumented = project_root / "demo" / "instrumented-v3-v4-1"
        blackbox = project_root / "demo" / "standalone-langgraph-v1-v2"

        self.assertTrue(verify_demo(instrumented)["valid"])
        repository = DashboardRepository(instrumented)
        experiment = repository.latest_experiment()
        self.assertEqual(repository.dashboard()["trial_count"], 66)
        self.assertEqual(repository.latest_gate()["decision"]["status"], "promote")
        self.assertIn("baseline", experiment["comparison"])
        self.assertIn("candidate", experiment["comparison"])
        attribution = experiment["comparison"]["failure_attribution"]
        self.assertIn("baseline", attribution)
        self.assertIn("candidate", attribution)

        has_parent_child_trace = False
        for trial in repository.trials():
            events = repository.trial(trial["id"])["trace"]
            span_ids = {event.get("span_id") for event in events if event.get("span_id")}
            if any(event.get("parent_span_id") in span_ids for event in events):
                has_parent_child_trace = True
                break
        self.assertTrue(has_parent_child_trace, "instrumented Demo must contain a hierarchical Trace")

        self.assertTrue(verify_demo(blackbox)["valid"])
        blackbox_repository = DashboardRepository(blackbox)
        self.assertEqual(blackbox_repository.dashboard()["trial_count"], 6)
        self.assertEqual(blackbox_repository.latest_gate()["decision"]["status"], "hold")
        self.assertTrue(all(row["tool_calls"] is None for row in blackbox_repository.trials()))
        blackbox_attribution = blackbox_repository.latest_experiment()["comparison"]["failure_attribution"]
        attributed_failures = sum(
            arm["counts"].get(kind, 0)
            for arm in blackbox_attribution.values()
            for kind in ("agent", "model", "infrastructure", "evidence", "policy")
        )
        self.assertGreater(attributed_failures, 0, "HOLD Demo must show a non-zero failure attribution")

    def test_export_keeps_selected_console_evidence_and_redacts_local_paths(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"; trial = source / "candidate" / "case_trial_001"; trial.mkdir(parents=True)
            trace = trial / "trace.jsonl"; trace.write_text(json.dumps({
                "kind": "span_start", "name": "agent.run",
                "attributes": {"output": "/Users/demo/private.py", "token": "sk-secret-value-123456"},
            }) + "\n", encoding="utf-8")
            (trial / "result.json").write_text(json.dumps({
                "trial_id": "case_trial_001", "status": "completed", "trace_path": str(trace),
                "evaluation": {"passed": True}, "agent_command": "/Users/demo/agent.py", "token": "sk-secret-value-123456",
            }), encoding="utf-8")
            attempt = trial / "attempts" / "attempt_001"; attempt.mkdir(parents=True)
            (attempt / "result.json").write_text("{}", encoding="utf-8")
            (source / "experiment.json").write_text(json.dumps({"evolution_catalog": "/Users/demo/catalog.json"}), encoding="utf-8")
            catalog = root / "catalog.json"; catalog.write_text(json.dumps({"schema_version": 1, "artifact_root": "/Users/demo/runtime"}), encoding="utf-8")

            output = root / "public-demo"
            manifest = export_demo(source, output, catalog=catalog)

            self.assertEqual(manifest["trial_count"], 1)
            self.assertFalse((output / "candidate/case_trial_001/attempts").exists())
            exported = json.loads((output / "candidate/case_trial_001/result.json").read_text(encoding="utf-8"))
            self.assertEqual(exported["trace_path"], "trace.jsonl")
            self.assertEqual(exported["agent_command"], "<local-path>")
            self.assertEqual(exported["token"], "[REDACTED]")
            exported_trace = (output / "candidate/case_trial_001/trace.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("/Users/demo", exported_trace)
            self.assertNotIn("sk-secret", exported_trace)
            self.assertEqual(json.loads((output / "experiment.json").read_text(encoding="utf-8"))["evolution_catalog"], "evolution-catalog.json")
            self.assertEqual(DashboardRepository(output).trial("candidate/case_trial_001")["trace"][0]["name"], "agent.run")
            self.assertTrue(verify_demo(output)["valid"])

            (output / "experiment.json").write_text("{}", encoding="utf-8")
            verification = verify_demo(output)
            self.assertFalse(verification["valid"])
            self.assertIn("digest mismatch: experiment.json", verification["errors"])

    def test_export_refuses_to_overwrite_existing_destination(self):
        with TemporaryDirectory() as directory:
            root = Path(directory); source = root / "source"; source.mkdir(); (source / "experiment.json").write_text("{}", encoding="utf-8")
            output = root / "output"; output.mkdir()
            with self.assertRaisesRegex(ValueError, "already exists"):
                export_demo(source, output)


if __name__ == "__main__":
    unittest.main()
