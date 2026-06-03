# Crypto cross-platform strategy spec


## 中文摘要

- 完整中文版见 [`crypto_cross_platform_strategy_spec.zh-CN.md`](crypto_cross_platform_strategy_spec.zh-CN.md)；本节保留在英文文件顶部，方便从当前文件直接找到中文入口。
- 用途：本文档围绕 `Crypto cross-platform strategy spec`，用于理解 `CryptoStrategies` 的配置、运行、部署、研究或验收边界。
- 主要覆盖：`Canonical required inputs`、`Target mode`、`Runtime adapters`、`Allowed and forbidden boundaries`、`Current rollout`。
- 阅读顺序：先确认边界、输入输出和权限要求，再执行文档里的命令、CI、dry-run、发布或切换步骤。
- 风险提示：涉及实盘、密钥、权限、Cloud Run、交易所或券商 API 的变更，必须先在测试环境或 dry-run 验证；不要只凭示例直接修改生产。
- 英文正文保留更完整的命令、字段名和配置键；如果摘要和正文不一致，以正文中的实际命令和配置为准。
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
- `universe_snapshot`: ordered official live-pool symbols from the validated `CryptoLivePoolPipelines` artifact for this cycle

## Target mode

Crypto profiles must declare exactly one `target_mode`.

Current default is:

- `crypto_live_pool_rotation` -> `weight`

Downstream platforms should translate only at the runtime boundary. Strategy code must not emit exchange-specific order fields.

## Runtime adapters

Every compatible platform must expose a runtime adapter for each profile.

A crypto runtime adapter must declare at least:

- `available_inputs`
- `available_capabilities`
- `portfolio_input_name` when the strategy needs `ctx.portfolio`
- `artifact_contract` when the strategy consumes upstream artifacts

`crypto_live_pool_rotation` currently declares an explicit artifact contract:

- `requires_snapshot_artifacts = true`
- `requires_snapshot_manifest_path = true`
- `snapshot_contract_version = crypto_live_pool_rotation.live_pool.v1`
- `config_source_policy = none`

The strategy package owns this declaration. Downstream platforms may decide how
to fetch the artifact, but they should not infer artifact requirements from
profile-name branches.

## Live-pool authority boundary

For `crypto_live_pool_rotation`, `CryptoLivePoolPipelines` is the authority for monthly live-pool membership, ranking, and order. The execution platform validates and preserves the ordered `live_pool.json["symbols"]` list, then passes it into `StrategyContext.market_data["universe_snapshot"]`.

Strategy code may apply runtime gates, sell rules, top-N selection, inverse-volatility sizing, BTC core allocation, and buy-budget allocation inside that upstream pool. It must not rebuild the monthly live pool from local indicators, replace the upstream order with a local ranking, or treat research CSVs as a substitute for the validated artifact contract.

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
- local monthly live-pool rebuilds for snapshot-backed profiles

## Current rollout

Today only one profile is live:

- `crypto_live_pool_rotation`

Today only one platform adapter exists:

- `binance`

The contract is still written in multi-strategy and multi-platform form so future crypto profiles can follow the same path without binding to Binance-only input names.
