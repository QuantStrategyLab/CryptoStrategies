from pathlib import Path


def test_drift_workflow_wires_real_pipeline_inputs_and_preflight_bundle() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "drift-check.yml").read_text(encoding="utf-8")

    assert "preflight_backtests:" in workflow
    assert "needs: preflight_backtests" in workflow
    assert "snapshot_repository_ref: ${{ steps.snapshot-input.outputs.snapshot_repository_ref }}" in workflow
    assert "id: snapshot-input" in workflow
    assert 'print(run["head_sha"])' in workflow
    assert "snapshot_repository_ref: ${{ needs.preflight_backtests.outputs.snapshot_repository_ref }}" in workflow
    assert "Download latest trusted lifecycle inputs" in workflow
    assert "gh api --paginate --slurp" in workflow
    assert "trusted-snapshot-runs.json" in workflow
    assert "crypto-lifecycle-inputs-" in workflow
    assert '"path": ".github/workflows/publish-lifecycle-inputs.yml"' in workflow
    assert '"conclusion": "success"' in workflow
    assert "research_panel.csv.gz" in workflow
    assert "market_history.csv.gz" in workflow
    assert "repository: QuantStrategyLab/QuantPlatformKit" in workflow
    assert "ref: e21547bf3074ba282871f0a245466a57bba89fcb" in workflow
    assert "python -m pip install --no-deps -e external/QuantPlatformKit" in workflow
    assert "scripts/run_walk_forward_backtest.py" in workflow
    assert '"--list-profiles"' in workflow
    assert '"--panel"' in workflow
    assert '"--market-history"' in workflow
    assert '"--returns-output"' in workflow
    assert "Upload lifecycle preflight artifact" in workflow
    assert "lifecycle-preflight-${{ github.run_id }}-${{ github.run_attempt }}" in workflow
    assert workflow.count("github.ref == format('refs/heads/{0}', github.event.repository.default_branch)") == 2
    assert "uses: QuantStrategyLab/QuantPlatformKit/.github/workflows/reusable-drift-check.yml@e21547bf3074ba282871f0a245466a57bba89fcb" in workflow
    assert "strategy_domain: crypto" in workflow
    assert "snapshot_repository: QuantStrategyLab/CryptoLivePoolPipelines" in workflow
    assert "snapshot_checkout_path: external/CryptoLivePoolPipelines" in workflow
    assert "lifecycle_preflight_artifact: lifecycle-preflight-${{ github.run_id }}-${{ github.run_attempt }}" in workflow
    assert "codex_audit_service_url: ${{ secrets.CODEX_AUDIT_SERVICE_URL }}" in workflow
    assert "secrets.SNAPSHOT_REPOSITORY_TOKEN || secrets.QSL_REPO_SYNC_TOKEN || github.token" in workflow
