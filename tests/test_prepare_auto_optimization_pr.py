from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.prepare_auto_optimization_pr import build_payload, evaluate_changed_files, parse_actions, render_pr_body


class PrepareAutoOptimizationPrTests(unittest.TestCase):
    def setUp(self) -> None:
        self.issue_context = {
            "number": 15,
            "title": "Monthly Optimization Tasks · Sandbox",
            "body": """# Monthly Optimization Tasks · Sandbox

## Actions
- [ ] `high` Investigate an upstream issue
  - Summary: Manual follow-up only.
  - Source: [Sandbox #0](https://example.com/issues/0)
- [ ] `low` Add a short README note [auto-pr-safe]
  - Summary: Document a small operator-facing behavior.
  - Source: [Sandbox #1](https://example.com/issues/1)
- [ ] `low` Add a boundary tracker [auto-pr-safe, experiment-only]
  - Summary: Track near-cutoff symbols monthly.
  - Source: [Sandbox #2](https://example.com/issues/2)
""",
        }

    def test_parse_actions_preserves_risk_flags_and_source(self) -> None:
        actions = parse_actions(self.issue_context["body"])

        self.assertEqual(len(actions), 3)
        self.assertEqual(actions[0]["risk_level"], "high")
        self.assertEqual(actions[1]["flags"], ["auto-pr-safe"])
        self.assertEqual(actions[2]["flags"], ["auto-pr-safe", "experiment-only"])
        self.assertEqual(actions[2]["source_label"], "Sandbox #2")

    def test_build_payload_selects_readme_note_as_auto_merge_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir) / "CryptoStrategies"
            repo_root.mkdir()
            payload = build_payload(self.issue_context, repo_root=repo_root)

        self.assertTrue(payload["should_run"])
        self.assertEqual(payload["safe_task_count"], 1)
        self.assertEqual(payload["auto_merge_candidate_count"], 1)
        self.assertEqual(payload["draft_only_task_count"], 0)
        self.assertTrue(payload["task_level_auto_merge_allowed"])
        self.assertEqual(payload["branch_name"], "automation/monthly-optimization-issue-15")
        self.assertEqual(payload["safe_actions"][0]["title"], "Add a short README note")

    def test_build_payload_keeps_shared_strategy_logic_tasks_as_draft_only(self) -> None:
        issue_context = {
            "number": 18,
            "title": "Monthly Optimization Tasks · Sandbox",
            "body": """# Monthly Optimization Tasks · Sandbox

## Actions
- [ ] `low` Document strategy signal thresholds [auto-pr-safe]
  - Summary: Document the strategy threshold handling for shared strategy modules.
  - Source: [Sandbox #3](https://example.com/issues/3)
""",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir) / "CryptoStrategies"
            repo_root.mkdir()
            payload = build_payload(issue_context, repo_root=repo_root)

        self.assertTrue(payload["should_run"])
        self.assertEqual(payload["auto_merge_candidate_count"], 0)
        self.assertEqual(payload["draft_only_task_count"], 1)
        self.assertFalse(payload["task_level_auto_merge_allowed"])
        self.assertIn("guarded_keyword:strategy", payload["draft_only_actions"][0]["auto_merge_blocker"])

    def test_evaluate_changed_files_blocks_shared_strategy_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            allowed = evaluate_changed_files(["README.md", "tests/test_catalog.py"], repo_root=repo_root)
            repo_root = Path('/Users/lisiyi/Projects/CryptoStrategies')
            blocked = evaluate_changed_files(["src/crypto_strategies/catalog.py", "README.md"], repo_root=repo_root)

        self.assertTrue(allowed["allowed"])
        self.assertFalse(blocked["allowed"])
        self.assertEqual(blocked["blocked_files"], ["src/crypto_strategies/catalog.py"])

    def test_evaluate_changed_files_blocks_binance_runtime_boundary_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir) / "BinancePlatform"
            repo_root.mkdir()
            blocked = evaluate_changed_files(["strategy_runtime.py", "decision_mapper.py"], repo_root=repo_root)

        self.assertFalse(blocked["allowed"])
        self.assertEqual(blocked["blocked_files"], ["strategy_runtime.py", "decision_mapper.py"])

    def test_render_pr_body_contains_merge_policy_and_issue_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = build_payload(self.issue_context, repo_root=Path(temp_dir))
        body = render_pr_body(payload)

        self.assertIn("<!-- auto-optimization-pr:issue-15 -->", body)
        self.assertIn("Task-level auto-merge eligible: `yes`", body)
        self.assertIn("Add a short README note", body)
        self.assertIn("Refs #15", body)


if __name__ == "__main__":
    unittest.main()
