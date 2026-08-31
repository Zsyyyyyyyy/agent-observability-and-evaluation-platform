import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from regression_lab.evolution import (
    evaluation_context_hash,
    validate_agent,
    validate_evolution_document,
    validate_experiment,
    validate_trial,
)
from regression_lab.evolution_catalog import EvolutionCatalog
from scripts.apply_evolution_lineage import apply_lineage


TIMESTAMP = "2026-08-12T12:00:00Z"


def version(version_id="external-openai-v1", parent=None, status="champion"):
    return {
        "version_id": version_id,
        "agent_id": "external-openai",
        "version": version_id,
        "parent_version_id": parent,
        "status": status,
        "change_type": "prompt",
        "change_summary": "Initial operating profile",
        "created_at": TIMESTAMP,
        "snapshot": {
            "adapter_id": "external-command",
            "model": "qwen3.6-plus",
            "prompt_profile": "direct-repair-v1",
            "toolset_hash": "sha256:tools",
            "config_hash": "sha256:config",
        },
    }


class EvolutionSchemaTests(unittest.TestCase):
    def test_context_hash_is_order_independent(self):
        self.assertEqual(
            evaluation_context_hash({"b": 2, "a": [1, 2]}),
            evaluation_context_hash({"a": [1, 2], "b": 2}),
        )
        self.assertTrue(evaluation_context_hash({"a": 1}).startswith("sha256:"))

    def test_entity_validation_rejects_invalid_status_and_timestamp(self):
        result = validate_agent({"agent_id": "../escape", "display_name": "x", "kind": "unknown", "created_at": "today", "metadata": {}})
        self.assertFalse(result.valid)
        self.assertGreaterEqual(len(result.errors), 3)

    def test_experiment_requires_matching_context_hash(self):
        experiment = {
            "experiment_id": "exp-001",
            "name": "v1 to v2",
            "baseline_version_id": "external-openai-v1",
            "candidate_version_id": "external-openai-v2",
            "status": "completed",
            "created_at": TIMESTAMP,
            "completed_at": TIMESTAMP,
            "case_ids": ["smoke-calculator"],
            "evaluation_context": {"case_ids": ["smoke-calculator"], "evaluator_version": "v2"},
            "evaluation_context_hash": "sha256:wrong",
            "evaluator_version": "evaluators-v2",
            "gate_policy_version": "gate-v2",
            "artifact_root": ".runtime/exp-001",
        }
        result = validate_experiment(experiment)
        self.assertFalse(result.valid)
        self.assertIn("evaluation_context_hash does not match evaluation_context", result.errors)

    def test_completed_trial_requires_selected_attempt(self):
        result = validate_trial({
            "trial_id": "trial-001", "experiment_id": "exp-001", "case_id": "smoke-calculator",
            "agent_version_id": "external-openai-v1", "trial_index": 1,
            "status": "completed", "attempt_ids": ["attempt-001"],
        })
        self.assertFalse(result.valid)
        self.assertIn("completed trial requires selected_attempt_id", result.errors)

    def test_document_checks_cross_entity_references(self):
        context = {"case_ids": ["smoke-calculator"], "evaluator_version": "v2"}
        document = {
            "schema_version": 1,
            "agents": [{"agent_id": "external-openai", "display_name": "External OpenAI", "kind": "external", "created_at": TIMESTAMP, "metadata": {}}],
            "versions": [version()],
            "cases": [{"case_id": "smoke-calculator", "manifest_id": "smoke-calculator", "manifest_version": 1, "fixture_hash": "sha256:f", "test_hash": "sha256:t", "policy_hash": "sha256:p"}],
            "experiments": [{
                "experiment_id": "exp-001", "name": "v1", "baseline_version_id": "external-openai-v1", "candidate_version_id": "external-openai-v1",
                "status": "completed", "created_at": TIMESTAMP, "completed_at": TIMESTAMP, "case_ids": ["smoke-calculator"],
                "evaluation_context": context, "evaluation_context_hash": evaluation_context_hash(context),
                "evaluator_version": "evaluators-v2", "gate_policy_version": "gate-v2", "artifact_root": ".runtime/exp-001",
            }],
            "trials": [],
            "attempts": [],
            "gate_decisions": [{
                "gate_id": "gate-001", "experiment_id": "exp-001", "status": "promote",
                "policy_version": "gate-v2", "decided_at": TIMESTAMP,
                "rules": [{"name": "pass_at_3", "passed": True}], "evidence": {"report": "experiment.json"},
            }],
        }
        self.assertTrue(validate_evolution_document(document).valid)
        document["experiments"][0]["case_ids"] = ["missing-case"]
        result = validate_evolution_document(document)
        self.assertFalse(result.valid)
        self.assertTrue(any("unknown case" in error for error in result.errors))

    def test_document_rejects_version_parent_cycle(self):
        document = {
            "schema_version": 1,
            "agents": [{"agent_id": "external-openai", "display_name": "External OpenAI", "kind": "external", "created_at": TIMESTAMP, "metadata": {}}],
            "versions": [version("external-openai-v1", "external-openai-v2"), version("external-openai-v2", "external-openai-v1")],
            "cases": [], "experiments": [], "trials": [], "attempts": [], "gate_decisions": [],
        }
        result = validate_evolution_document(document)
        self.assertFalse(result.valid)
        self.assertTrue(any("creates a cycle" in error for error in result.errors))

    def test_catalog_indexes_attempt_history_and_gate_without_copying_artifacts(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "experiment"
            trial = root / "baseline" / "case" / "case_trial_001"
            first = trial / "attempts" / "attempt_001"; first.mkdir(parents=True)
            second = trial / "attempts" / "attempt_002"; second.mkdir()
            first_result = {"trial_id": "case_trial_001", "attempt_id": "attempt_001", "status": "trace_incomplete", "trace_id": "trace_old"}
            second_result = {"trial_id": "case_trial_001", "attempt_id": "attempt_002", "status": "completed", "trace_id": "trace_new", "adapter_id": "external-command"}
            (first / "result.json").write_text(__import__("json").dumps(first_result), encoding="utf-8")
            (second / "result.json").write_text(__import__("json").dumps(second_result), encoding="utf-8")
            (trial / "result.json").write_text(__import__("json").dumps(second_result), encoding="utf-8")
            (trial / "selected-attempt.json").write_text(__import__("json").dumps({"attempt_id": "attempt_001"}), encoding="utf-8")
            report = {
                "metrics_version": 2, "trial_count_required_per_case": 1,
                "agents": [{"id": "baseline", "version": "external-openai-v1"}, {"id": "candidate", "version": "external-openai-v2"}],
                "summaries": {
                    "baseline": {"jobs": [{"job_id": "case_trial_001", "case_id": "case", "trial_index": 1, "adapter_id": "external-command"}]},
                    "candidate": {"jobs": []},
                },
            }
            catalog = EvolutionCatalog(Path(directory) / "evolution-catalog.json")
            experiment_id = catalog.index_experiment(report, artifact_root=root, manifests=[{"id": "case", "version": 1, "fixture": {}, "task": {}, "tool_policy": {}}])
            gate_id = catalog.index_gate(experiment_id, {"passed": True, "decision": {"status": "promote"}, "rules": []}, policy_version="default-gate")
            document = catalog.load()

            self.assertEqual(len(document["attempts"]), 2)
            self.assertEqual(document["agents"][0]["kind"], "external")
            self.assertEqual(document["trials"][0]["status"], "completed")
            selected = document["trials"][0]["selected_attempt_id"]
            selected_row = next(row for row in document["attempts"] if row["attempt_id"] == selected)
            self.assertEqual(selected_row["source_attempt_id"], "attempt_001")
            self.assertEqual(document["gate_decisions"][0]["gate_id"], gate_id)
            self.assertTrue(validate_evolution_document(document).valid)
            self.assertEqual(catalog.history("external-openai")["experiments"][0]["experiment_id"], experiment_id)

    def test_explicit_lineage_is_preserved_when_artifacts_are_reindexed(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "experiment"
            for label in ("baseline", "candidate"):
                trial = root / label / "case" / "case_trial_001"
                trial.mkdir(parents=True)
                (trial / "result.json").write_text(__import__("json").dumps({
                    "trial_id": "case_trial_001", "attempt_id": "attempt_001", "status": "completed",
                    "trace_id": f"trace_{label}", "adapter_id": "external-command",
                }), encoding="utf-8")
            report = {
                "metrics_version": 3, "trial_count_required_per_case": 1,
                "agents": [{"id": "baseline", "version": "external-openai-v1"}, {"id": "candidate", "version": "external-openai-v2"}],
                "summaries": {
                    "baseline": {"jobs": [{"job_id": "case_trial_001", "case_id": "case", "trial_index": 1, "adapter_id": "external-command"}]},
                    "candidate": {"jobs": [{"job_id": "case_trial_001", "case_id": "case", "trial_index": 1, "adapter_id": "external-command"}]},
                },
            }
            catalog = EvolutionCatalog(Path(directory) / "evolution-catalog.json")
            catalog.index_experiment(report, artifact_root=root, manifests=[{"id": "case", "version": 1, "fixture": {}, "task": {}, "tool_policy": {}}])
            apply_lineage(catalog, {
                "schema_version": 1, "agent_id": "external-openai", "display_name": "External Agent",
                "versions": [
                    {"version": "external-openai-v1", "parent_version": None, "status": "champion", "change_type": "config", "change_summary": "base"},
                    {"version": "external-openai-v2", "parent_version": "external-openai-v1", "status": "candidate", "change_type": "prompt", "change_summary": "prompt", "prompt_profile": "observe-plan-act-verify-v2"},
                ],
            })
            catalog.index_experiment(report, artifact_root=root, manifests=[{"id": "case", "version": 1, "fixture": {}, "task": {}, "tool_policy": {}}])
            history = catalog.timeline()

        self.assertEqual([row["version"] for row in history["versions"]], ["external-openai-v1", "external-openai-v2"])
        self.assertEqual(history["versions"][1]["parent_version_id"], history["versions"][0]["version_id"])
        self.assertEqual(history["versions"][1]["change_summary"], "prompt")
        self.assertEqual(history["versions"][1]["snapshot"]["prompt_profile"], "observe-plan-act-verify-v2")

    def test_project_scoped_catalog_records_project_identity(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "experiment"
            trial = root / "baseline" / "case" / "case_trial_001"; trial.mkdir(parents=True)
            (trial / "result.json").write_text(json.dumps({"trial_id": "case_trial_001", "status": "completed"}), encoding="utf-8")
            report = {
                "metrics_version": 3, "trial_count_required_per_case": 1, "project_id": "coding-agent-platform",
                "agents": [{"id": "baseline", "version": "v1"}, {"id": "candidate", "version": "v2"}],
                "summaries": {"baseline": {"jobs": [{"job_id": "case_trial_001", "case_id": "case", "trial_index": 1}]}, "candidate": {"jobs": []}},
            }
            catalog = EvolutionCatalog(Path(directory) / "projects" / "coding-agent-platform" / "evolution-catalog.json")
            catalog.index_experiment(report, artifact_root=root, manifests=[{"id": "case", "version": 1, "fixture": {}, "task": {}, "tool_policy": {}}], project_id="coding-agent-platform")
            document = catalog.load()

        self.assertEqual(document["project"], {"project_id": "coding-agent-platform"})
        self.assertTrue(all(item["project_id"] == "coding-agent-platform" for item in document["versions"]))
        self.assertEqual(document["experiments"][0]["project_id"], "coding-agent-platform")
        identity = document["experiments"][0]["evaluation_context"]["identity"]
        self.assertEqual(identity["project_id"], "coding-agent-platform")
        self.assertEqual(identity["baseline_version"], "v1")
        self.assertEqual(identity["candidate_version"], "v2")


if __name__ == "__main__":
    unittest.main()
