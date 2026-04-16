# Crypto cross-platform strategy spec

This repository now follows the same contract split used by the US equity stack:

- `CryptoStrategies` owns pure strategy logic, manifests, metadata, and runtime adapters
- downstream platforms own exchange connectivity, market-data collection, artifact freshness checks, order placement, and notifications
- strategy code must only read `StrategyContext`
- platform-specific wiring must stay in the platform repo

## Canonical required inputs

New crypto profiles must declare `required_inputs` from this canonical set only:

- `market_prices`
- `derived_indicators`
- `benchmark_snapshot`
- `portfolio_snapshot`
- `universe_snapshot`

Current meaning for the live profile:

- `market_prices`: latest tradable prices keyed by symbol
- `derived_indicators`: strategy-ready trend metrics keyed by symbol
- `benchmark_snapshot`: benchmark regime snapshot, currently BTC
- `portfolio_snapshot`: exchange-agnostic portfolio and cash snapshot
- `universe_snapshot`: candidate tradable symbols for this cycle

## Target mode

Crypto profiles must declare exactly one `target_mode`.

Current default is:

- `crypto_leader_rotation` -> `weight`

Downstream platforms should translate only at the runtime boundary. Strategy code must not emit exchange-specific order fields.

## Runtime adapters

Every compatible platform must expose a runtime adapter for each profile.

A crypto runtime adapter must declare at least:

- `available_inputs`
- `available_capabilities`
- `portfolio_input_name` when the strategy needs `ctx.portfolio`
- `artifact_contract` when the strategy consumes upstream artifacts

`crypto_leader_rotation` currently declares an explicit artifact contract:

- `requires_snapshot_artifacts = true`
- `requires_snapshot_manifest_path = true`
- `snapshot_contract_version = crypto_leader_rotation.live_pool.v1`
- `config_source_policy = none`

The strategy package owns this declaration. Downstream platforms may decide how
to fetch the artifact, but they should not infer artifact requirements from
profile-name branches.

## Allowed and forbidden boundaries

Allowed inside strategy code:

- reading canonical inputs from `ctx.market_data`
- reading `ctx.portfolio`
- reading pure runtime knobs from `ctx.runtime_config`
- returning `StrategyDecision`

Forbidden inside strategy code:

- exchange branches such as Binance or future broker names
- direct environment reads
- exchange-specific order payloads
- artifact-path lookup and freshness validation

## Current rollout

Today only one profile is live:

- `crypto_leader_rotation`

Today only one platform adapter exists:

- `binance`

The contract is still written in multi-strategy and multi-platform form so future crypto profiles can follow the same path without binding to Binance-only input names.
