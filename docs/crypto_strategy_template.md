# Crypto strategy template

Use this template when adding a new crypto strategy profile.

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
