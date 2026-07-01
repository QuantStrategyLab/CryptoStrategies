from __future__ import annotations

import unittest

from scripts.gate_codex_app_review import scan_diff


class GateCodexAppReviewTests(unittest.TestCase):
    def test_scan_diff_redacts_hardcoded_secret_values(self) -> None:
        secret_field = "API" + "_KEY"
        secret_value = "super" + "secretvalue123456"
        diff = (
            "diff --git a/app.py b/app.py\n"
            "--- a/app.py\n"
            "+++ b/app.py\n"
            f'+{secret_field} = "{secret_value}"\n'
        )

        violations = scan_diff(diff, [])

        self.assertEqual(len(violations), 1)
        self.assertIn("<redacted>", violations[0])
        self.assertIn("api_key", violations[0])
        self.assertNotIn(secret_value, violations[0])


if __name__ == "__main__":
    unittest.main()
