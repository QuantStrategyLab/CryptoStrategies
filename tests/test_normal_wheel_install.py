"""Offline checks for provenance rejection and baseline-result classification."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

_CHECKER = Path(__file__).resolve().parents[1] / "scripts/check_normal_wheel_install.py"
_spec = importlib.util.spec_from_file_location("normal_wheel_checker", _CHECKER)
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)


def presence(*, numpy=False, pandas=False):
    return {
        name: {"distribution_present": installed, "module_present": installed}
        for name, installed in (("numpy", numpy), ("pandas", pandas))
    }


class NormalWheelClassificationTests(unittest.TestCase):
    def test_baseline_records_missing_import_without_claiming_runtime_success(self):
        result, code = checker.classify_import(
            ModuleNotFoundError("No module named 'pandas'", name="pandas"),
            presence(), [], "pandas",
        )
        self.assertEqual(code, 0)
        self.assertEqual(result, {
            "status": "expected_gap_reproduced",
            "runtime_import_succeeded": False,
            "expected_gap_reproduced": True,
            "missing_dependency": "pandas",
        })

    def test_strict_default_rejects_the_same_missing_dependency(self):
        result, code = checker.classify_import(
            ModuleNotFoundError(name="pandas"), presence(), [], None,
        )
        self.assertEqual(code, 1)
        self.assertFalse(result["runtime_import_succeeded"])
        self.assertFalse(result["expected_gap_reproduced"])
        self.assertEqual(result["status"], "runtime_import_failed")

    def test_network_attempt_overrides_an_otherwise_expected_gap(self):
        result, code = checker.classify_import(
            ModuleNotFoundError(name="pandas"), presence(), ["socket.connect"], "pandas",
        )
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "network_attempt_detected")
        self.assertFalse(result["expected_gap_reproduced"])

    def test_other_failures_are_never_accepted_as_the_expected_gap(self):
        class MissingSubclass(ModuleNotFoundError):
            pass

        for error in (
            ModuleNotFoundError(name="numpy"),
            ModuleNotFoundError(name="pandas._libs"),
            ModuleNotFoundError(name="google"),
            ModuleNotFoundError(),
            ImportError("pandas"),
            RuntimeError("pandas"),
            MissingSubclass(name="pandas"),
        ):
            with self.subTest(error=type(error).__name__, missing=getattr(error, "name", None)):
                result, code = checker.classify_import(error, presence(), [], "pandas")
                self.assertEqual(code, 1)
                self.assertFalse(result["expected_gap_reproduced"])

    def test_baseline_rejects_any_numeric_distribution_or_module_already_present(self):
        for module in ("numpy", "pandas"):
            for field in ("distribution_present", "module_present"):
                with self.subTest(module=module, field=field):
                    before = presence()
                    before[module][field] = True
                    result, code = checker.classify_import(
                        ModuleNotFoundError(name="pandas"), before, [], "pandas",
                    )
                    self.assertEqual(code, 1)
                    self.assertFalse(result["expected_gap_reproduced"])

    def test_strict_success_requires_distribution_and_module_metadata(self):
        result, code = checker.classify_import(None, presence(numpy=True, pandas=True), [], None)
        self.assertEqual(code, 0)
        self.assertTrue(result["runtime_import_succeeded"])
        self.assertFalse(result["expected_gap_reproduced"])
        for module in ("numpy", "pandas"):
            for field in ("distribution_present", "module_present"):
                with self.subTest(module=module, field=field):
                    before = presence(numpy=True, pandas=True)
                    before[module][field] = False
                    result, code = checker.classify_import(None, before, [], None)
                    self.assertEqual(code, 1)
                    self.assertEqual(result["status"], "numeric_distribution_metadata_missing")

    def test_baseline_mode_rejects_an_import_that_now_succeeds(self):
        result, code = checker.classify_import(None, presence(numpy=True, pandas=True), [], "pandas")
        self.assertEqual(code, 1)
        self.assertTrue(result["runtime_import_succeeded"])
        self.assertFalse(result["expected_gap_reproduced"])


class NormalWheelProvenanceTests(unittest.TestCase):
    def distribution(self, direct_url, location="/synthetic-venv/site-packages"):
        return SimpleNamespace(
            version="1.0.0",
            locate_file=lambda _path: Path(location),
            read_text=lambda _name: json.dumps(direct_url),
        )

    def test_qpk_requires_both_the_repository_and_exact_commit(self):
        pin = "a" * 40
        direct = {
            "url": "https://github.com/QuantStrategyLab/QuantPlatformKit.git",
            "vcs_info": {"vcs": "git", "commit_id": pin},
        }
        roots = (Path("/synthetic-venv/site-packages"),)
        with patch.object(checker.importlib.metadata, "distribution", return_value=self.distribution(direct)):
            self.assertEqual(checker._distribution("quant-platform-kit", roots, pin)["vcs_commit"], pin)
        for changed in (
            {**direct, "url": "https://example.invalid/QuantPlatformKit.git"},
            {**direct, "vcs_info": {"vcs": "git", "commit_id": "b" * 40}},
            {**direct, "vcs_info": {"vcs": "git", "requested_revision": pin}},
            {**direct, "dir_info": {"editable": True}},
        ):
            with self.subTest(direct_url=changed):
                with patch.object(checker.importlib.metadata, "distribution", return_value=self.distribution(changed)):
                    with self.assertRaises(checker.VerificationError):
                        checker._distribution("quant-platform-kit", roots, pin)

    def test_crypto_requires_a_wheel_and_rejects_a_source_archive(self):
        roots = (Path("/synthetic-venv/site-packages"),)
        for url in ("file:///build/crypto_strategies-0.4.11.tar.gz", "file:///source"):
            with self.subTest(url=url):
                distribution = self.distribution({"url": url, "archive_info": {}})
                with patch.object(checker.importlib.metadata, "distribution", return_value=distribution):
                    with self.assertRaises(checker.VerificationError):
                        checker._distribution("crypto-strategies", roots, "a" * 40)

    def test_distribution_and_module_origins_cannot_escape_the_virtualenv(self):
        roots = (Path("/synthetic-venv/site-packages"),)
        distribution = self.distribution({}, location="/sibling-source")
        with patch.object(checker.importlib.metadata, "distribution", return_value=distribution):
            with self.assertRaises(checker.VerificationError):
                checker._distribution("numpy", roots, "a" * 40)
        with patch.object(checker.importlib.util, "find_spec", return_value=SimpleNamespace(origin="/sibling-source/pandas.py")):
            with self.assertRaises(checker.VerificationError):
                checker._module_origin("pandas", roots)


if __name__ == "__main__":
    unittest.main()
