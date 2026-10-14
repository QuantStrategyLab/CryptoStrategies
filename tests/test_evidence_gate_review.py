import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_gate():
    spec = importlib.util.spec_from_file_location("gate_evidence_package", ROOT / "scripts" / "gate_evidence_package.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _install_reviewer(monkeypatch, result):
    calls = []
    module = types.ModuleType("quant_platform_kit.strategy_lifecycle.task_review")

    def review_material(material, *, operation_id, required_roles):
        calls.append((operation_id, tuple(required_roles)))
        if isinstance(result, Exception):
            raise result
        return result

    module.review_material = review_material
    monkeypatch.setitem(sys.modules, "quant_platform_kit.strategy_lifecycle.task_review", module)
    return calls


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("AI_SERVICE_RELEASE_READY", "true")
    monkeypatch.delenv("DUAL_REVIEW_GATE_SKIP", raising=False)
    path = tmp_path / "synthetic.json"
    path.write_text(json.dumps({"strategy_profile": "synthetic"}), encoding="utf-8")
    return path


def test_unanimous_advisory_approval_passes(monkeypatch, evidence):
    calls = _install_reviewer(monkeypatch, {"status": "completed", "outcome": "agree_approve", "advisory_only": True})
    assert _load_gate()._run_promotion_dual_review([evidence]) == 0
    assert len(calls) == 1
    assert calls[0][1] == ("primary", "secondary", "verification")


@pytest.mark.parametrize("result,code", [
    ({"status": "unavailable", "reason": "review_routes_incomplete", "advisory_only": True}, 3),
    ({"status": "pending", "tasks": [], "advisory_only": True}, 3),
    (RuntimeError("service down"), 3),
    ({"status": "completed", "outcome": "requires_human", "advisory_only": True}, 1),
    ({"status": "completed", "outcome": "agree_reject", "advisory_only": True}, 1),
    ({"status": "completed", "outcome": "agree_approve"}, 1),
])
def test_review_fails_closed(monkeypatch, evidence, result, code):
    _install_reviewer(monkeypatch, result)
    assert _load_gate()._run_promotion_dual_review([evidence]) == code


def test_missing_caller_revision_fails_closed(monkeypatch, evidence):
    _install_reviewer(monkeypatch, {"status": "completed", "outcome": "agree_approve", "advisory_only": True})
    monkeypatch.setenv("GITHUB_SHA", "not-a-sha")
    assert _load_gate()._run_promotion_dual_review([evidence]) == 3


def test_workflow_uses_v2_task_service_only():
    workflow = (ROOT / ".github" / "workflows" / "evidence-gate.yml").read_text(encoding="utf-8")
    for retired in ("AIAuditBridge", "CODEX_AUDIT_SERVICE_URL", "AI_GATEWAY_SERVICE_URL"):
        assert retired not in workflow
    assert "AI_SERVICE_URL: ${{ vars.AI_SERVICE_URL }}" in workflow
    assert "id-token: write" in workflow


@pytest.mark.parametrize("ready", [None, "", "false", "1", "yes", "invalid"])
@pytest.mark.parametrize("skip", ["", "true"])
def test_release_not_ready_blocks_before_dispatch(monkeypatch, evidence, ready, skip):
    calls = _install_reviewer(monkeypatch, {"status": "completed", "outcome": "agree_approve", "advisory_only": True})
    if ready is None:
        monkeypatch.delenv("AI_SERVICE_RELEASE_READY", raising=False)
    else:
        monkeypatch.setenv("AI_SERVICE_RELEASE_READY", ready)
    monkeypatch.setenv("DUAL_REVIEW_GATE_SKIP", skip)
    assert _load_gate()._run_promotion_dual_review([evidence]) == 3
    assert calls == []


def test_nonpromotion_diff_needs_no_ai_release(monkeypatch):
    gate = _load_gate()
    monkeypatch.setattr(gate, "_git_diff", lambda _: "+# documentation only")
    monkeypatch.delenv("AI_SERVICE_RELEASE_READY", raising=False)
    assert gate.main() == 0


def test_workflow_passes_release_readiness():
    workflow = (ROOT / ".github" / "workflows" / "evidence-gate.yml").read_text(encoding="utf-8")
    assert "AI_SERVICE_RELEASE_READY: ${{ vars.AI_SERVICE_RELEASE_READY }}" in workflow
