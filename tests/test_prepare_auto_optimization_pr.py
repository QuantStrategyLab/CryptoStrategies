from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.prepare_auto_optimization_pr import build_payload, parse_actions, render_pr_body


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
- [ ] `low` Add zero-trade diagnostics to the report [auto-pr-safe]
  - Summary: Include the top failed gating reason counts.
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

    def test_build_payload_selects_only_non_experiment_low_auto_pr_safe_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = build_payload(self.issue_context, repo_root=Path(temp_dir))

        self.assertTrue(payload["should_run"])
        self.assertEqual(payload["safe_task_count"], 1)
        self.assertEqual(payload["skipped_task_count"], 0)
        self.assertEqual(payload["branch_name"], "automation/monthly-optimization-issue-15")
        self.assertEqual(payload["safe_actions"][0]["title"], "Add zero-trade diagnostics to the report")

    def test_render_pr_body_contains_marker_and_issue_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = build_payload(self.issue_context, repo_root=Path(temp_dir))
        body = render_pr_body(payload)

        self.assertIn("<!-- auto-optimization-pr:issue-15 -->", body)
        self.assertIn("Add zero-trade diagnostics to the report", body)
        self.assertIn("Refs #15", body)


if __name__ == "__main__":
    unittest.main()
