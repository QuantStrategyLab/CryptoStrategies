from pathlib import Path


def test_drift_workflow_wires_real_pipeline_inputs_and_preflight_bundle() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "drift-check.yml").read_text(encoding="utf-8")

    assert "preflight_only:" in workflow
    assert "type: boolean" in workflow
    assert "default: false" in workflow
    assert "inputs.preflight_only != true" in workflow
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
    assert (
        "repository: QuantStrategyLab/QuantPlatformKit\n"
        "          ref: eae362bd3e3fad5be59a7615eeba10be839a9934\n"
        "          path: external/QuantPlatformKit"
    ) in workflow
    assert "quant_platform_kit_ref: eae362bd3e3fad5be59a7615eeba10be839a9934" in workflow
    assert "python -m pip install --no-deps -e external/QuantPlatformKit" in workflow
    assert "python scripts/build_lifecycle_preflight.py" in workflow
    assert '--panel "$INPUT_ROOT/research_panel.csv.gz"' in workflow
    assert '--market-history "$INPUT_ROOT/market_history.csv.gz"' in workflow
    assert '--bundle-root "$LIFECYCLE_PREFLIGHT_BUNDLE_ROOT"' in workflow
    assert "Upload lifecycle preflight artifact" in workflow
    assert "lifecycle-preflight-${{ github.run_id }}-${{ github.run_attempt }}" in workflow
    assert workflow.count("github.ref == format('refs/heads/{0}', github.event.repository.default_branch)") == 2
    assert "uses: QuantStrategyLab/QuantPlatformKit/.github/workflows/reusable-drift-check.yml@474b701dabcb910f085bfff25a41daf5c0a52a5b" in workflow
    assert "strategy_domain: crypto" in workflow
    assert "snapshot_repository: QuantStrategyLab/CryptoLivePoolPipelines" in workflow
    assert "snapshot_checkout_path: external/CryptoLivePoolPipelines" in workflow
    # A failed-job rerun reuses the successful preflight job's original output.
    producer, consumer = workflow.split("  drift:", 1)
    assert "lifecycle_preflight_artifact: lifecycle-preflight-${{ github.run_id }}-${{ github.run_attempt }}" in producer
    assert "lifecycle_preflight_artifact: ${{ needs.preflight_backtests.outputs.lifecycle_preflight_artifact }}" in consumer
    assert "github.run_attempt" not in consumer
    # Retired v1 Codex/GPT/Claude reviewer wiring must not return.
    assert "codex_audit_service_url" not in workflow
    assert "ai_gateway_service_url" not in workflow
    # V2 project-scoped task service; provider (e.g. dot) is chosen by the
    # service-side project policy, not by this caller.
    assert "ai_service_url: ${{ vars.AI_SERVICE_URL }}" in workflow
    assert "ai_service_audience: ${{ vars.AI_SERVICE_AUDIENCE }}" in workflow
    assert "ai_service_reviewers_json: ${{ vars.AI_SERVICE_REVIEWERS_JSON }}" in workflow
    assert "id-token: write" in workflow
    assert "secrets.SNAPSHOT_REPOSITORY_TOKEN || secrets.QSL_REPO_SYNC_TOKEN || github.token" in workflow
