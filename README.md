# CryptoStrategies

[Chinese README](README.zh-CN.md)

> Investing involves risk. This project does not provide investment advice and is for education, research, and engineering review only.

## What this repository is

CryptoStrategies is the QuantStrategyLab crypto strategy package. It provides shared crypto strategy implementation and runtime metadata for the Binance execution platform.

It is one layer of a multi-repository system:

- **Strategy packages**: hold reusable strategy code, metadata, and runtime entrypoints.
- **Snapshot pipelines**: produce authoritative live-pool snapshots, rankings, backtests, and release evidence.
- **Platform runtimes**: connect strategies to brokers, dry-run checks, notifications, and live deployment controls.
- **Shared infrastructure**: keeps contracts, settings, adapters, plugins, and audit workflows reusable across repositories.

This repository owns strategy code and metadata. For snapshot-backed crypto profiles, it consumes the upstream live pool and does not rebuild monthly pool membership or ordering locally. It does not hold broker credentials, submit orders by itself, or replace the live-pool/release evidence required before a profile is enabled for live runtime settings.

## Strategy profiles

### Direct runtime strategies

These profiles can run from market history, portfolio snapshots, or other runtime inputs without a separate feature-snapshot build step.

No direct runtime strategies are exposed from this package.

### Snapshot-backed strategies

These profiles depend on artifacts produced by `CryptoLivePoolPipelines` before downstream platforms should use them.

| Profile | Name | Notes |
| --- | --- | --- |
| `crypto_live_pool_rotation` | Crypto Live Pool Rotation | runtime-enabled trend-following rotation that consumes the ordered live pool published by CryptoLivePoolPipelines. The legacy `crypto_leader_rotation` profile remains an alias for compatibility. Runtime code may gate and size trades inside that pool, but monthly selection and order remain upstream. |

### Research-only candidates

Research-only profiles may stay in code for reproducibility and future review, but they should not appear in current configurable live profiles.

No direct runtime strategies are exposed from this package.

## How this connects to execution

Execution platforms consume this package through strategy loaders and runtime metadata. Current downstream platforms: BinancePlatform.

Use the platform repositories for broker credentials, dry-run/live switches, order submission, and deployment settings.

## Evidence and live enablement

Use this README as a map of the project, not as live performance data. Before enabling or changing a live profile, rerun the relevant live-pool/release pipeline and review short, medium, and long windows: return, max drawdown, benchmark-relative return, turnover, data freshness, and artifact version. Monthly live-pool selection, ranking, and promotion evidence belong in CryptoLivePoolPipelines; strategy changes here should preserve that upstream authority. If evidence is stale, incomplete, or the profile is marked research-only, keep it out of live runtime settings.

## Repository layout

- `src/`: library and runtime code.
- `tests/`: unit, contract, and regression tests.
- `docs/`: runbooks, design notes, evidence, and integration contracts.
- `.github/workflows/`: CI, scheduled jobs, release, or deployment workflows.

## Quick start

```bash
python -m pip install -e .
python -m pytest -q
```

## Useful docs

- [`docs/crypto_cross_platform_strategy_spec.md`](docs/crypto_cross_platform_strategy_spec.md)
- [`docs/crypto_cross_platform_strategy_spec.zh-CN.md`](docs/crypto_cross_platform_strategy_spec.zh-CN.md)
- [`docs/crypto_portability_checklist.md`](docs/crypto_portability_checklist.md)
- [`docs/crypto_portability_checklist.zh-CN.md`](docs/crypto_portability_checklist.zh-CN.md)
- [`docs/crypto_strategy_template.md`](docs/crypto_strategy_template.md)
- [`docs/crypto_strategy_template.zh-CN.md`](docs/crypto_strategy_template.zh-CN.md)

## Safety and contribution notes

- Keep secrets, account identifiers, tokens, cookies, and broker credentials out of Git and logs.
- Prefer small, reviewable changes with tests or reproducible evidence.
- For strategy changes, include the command or artifact used to validate behavior.

## Community and security

- See [CONTRIBUTING.md](CONTRIBUTING.md) for pull request scope, local verification, and documentation expectations.
- Follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for maintainer and contributor conduct.
- Report credential, automation, broker, exchange, or cloud-resource vulnerabilities through [SECURITY.md](SECURITY.md); do not open public issues for secrets or live-execution risk.

## License

See [LICENSE](LICENSE).
