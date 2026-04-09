# Crypto strategy template

Use this template when adding a new crypto strategy profile.

## Minimum layout

At minimum, a new profile should touch these places:

- `src/crypto_strategies/catalog.py`
- `src/crypto_strategies/manifests/__init__.py`
- `src/crypto_strategies/entrypoints/__init__.py`
- `src/crypto_strategies/runtime_adapters.py`
- one implementation module under `src/crypto_strategies/strategies/`
- tests under `tests/`

## Minimum checklist

1. add `StrategyDefinition` in `src/crypto_strategies/catalog.py`
2. add `StrategyManifest` in `src/crypto_strategies/manifests/__init__.py`
3. add a unified entrypoint in `src/crypto_strategies/entrypoints/__init__.py`
4. add a runtime adapter in `src/crypto_strategies/runtime_adapters.py`
5. add catalog and entrypoint tests
6. if the profile becomes live, add platform status and portability checks downstream

## StrategyDefinition

Every new profile must declare:

- `profile`
- `domain="crypto"`
- `supported_platforms`
- `entrypoint`
- `required_inputs`
- `default_config`
- `target_mode`

`required_inputs` must use canonical names only.

## Manifest and entrypoint

The manifest and the catalog entry must agree on:

- `profile`
- `domain`
- `display_name`
- `required_inputs`
- `default_config`

The entrypoint must:

- read only canonical inputs from `StrategyContext`
- read `ctx.portfolio` when portfolio data is required
- return only `StrategyDecision`
- keep exchange-specific execution details out of the strategy repo

## Runtime adapter

A new runtime adapter must declare at least:

- `available_inputs`
- `portfolio_input_name` when the strategy reads `ctx.portfolio`

If a platform does not support the profile yet, do not add it to `supported_platforms`.

## Forbidden shortcuts

Do not:

- branch on platform names inside strategy code
- read exchange env vars inside strategy code
- return Binance-specific order payload fields
- bind downstream code to internal helper module names

## Minimum tests

- catalog test
- entrypoint test
- governance test for canonical inputs and explicit target mode
- downstream adapter smoke test before enabling a new platform

## New strategy PR checklist

- [ ] `StrategyDefinition` added with explicit `target_mode`
- [ ] `StrategyManifest` matches the catalog definition
- [ ] entrypoint reads only canonical inputs
- [ ] runtime adapter added for every compatible platform
- [ ] strategy code has no platform branch or env reads
- [ ] catalog, entrypoint, and governance tests were updated
- [ ] downstream platform status script or adapter smoke was updated when rollout changed
