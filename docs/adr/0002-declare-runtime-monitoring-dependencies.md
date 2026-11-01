# ADR 0002: Declare runtime monitoring dependencies

Status: proposed; dependency adoption requires owner approval

## Context

A normal installation should provide the libraries needed to import its
supported runtime entrypoints. Research extras and the developer test
environment serve different purposes and should not supply an undeclared
requirement of that import path.

| Boundary | Evidence |
| --- | --- |
| Consumer metadata | [The baseline Crypto declaration][crypto-deps] lists QPK as the only normal dependency. NumPy and Pandas appear in the optional `research` extra. |
| Consumer import | [The common runtime entrypoint][crypto-common] imports QPK's `PerformanceMonitor` at module scope. |
| Consumed package metadata | [The pinned QPK declaration][qpk-deps] has an empty normal dependency list. NumPy and Pandas appear only in its `dev` extra. |
| Consumed import path | [The monitor][qpk-monitor] imports Pandas and its metrics module. [The metrics module][qpk-metrics] imports both NumPy and Pandas at module scope. |
| Existing verification | [CI installation][ci-install] explicitly installs NumPy and Pandas. [The catalog/entrypoint check][entrypoint-check] skips when the import reports missing Pandas. |

The QPK links identify the consumed revision,
`c7646a7168b3dafa763ef7751a182d23e8de7790`. Passing tests in an environment that
separately installs these libraries does not establish that normal package
metadata is sufficient. No production failure is inferred from this finding.

## Clean normal-wheel reproduction

[Diagnostic draft #299][baseline-pr] measured the unchanged package metadata and
runtime source from Crypto commit
`ec0b330081213f56c3ff98164ec00ba26c10dfc9`. Its test-only head was
`7025582e5ba99781197379415abbc9a8001a78a0`.

Each job built `crypto_strategies-0.4.11-py3-none-any.whl`, created a new virtual
environment, and ran normal `pip install` on that wheel. Neither job requested
extras, manually installed NumPy or Pandas, used an editable installation, or
added a sibling source checkout. The import probe ran with `python -I` outside
the checkout and checked installed distribution and module origins. A network
audit guard covered the import; no strategy was evaluated.

| Environment | Normal install | `pip check` | Public entrypoint import |
| --- | --- | --- | --- |
| [Python 3.11][baseline-311] | Succeeded | Passed | `ModuleNotFoundError`, missing `pandas` |
| [Python 3.12][baseline-312] | Succeeded | Passed | `ModuleNotFoundError`, missing `pandas` |

Both reports recorded NumPy and Pandas distributions and modules as absent,
Crypto and QPK under the fresh environment's `site-packages`, the exact QPK pin,
and zero socket or DNS attempts. Both wheel builds had SHA-256
`f990c3b458beb8e1f8ea07e9ad2acae58da85ba3efb956d9ad0f4618f976afc9`.
The baseline measurement reported `runtime_import_succeeded: false`; its green
diagnostic job means that the specified gap was reproduced. It does not mean the
runtime import worked. The dependency proposal removes that diagnostic
acceptance mode and requires a successful import.

A preceding executor installation stopped while fetching QPK because its
configured proxy was unreachable. That was an environment failure, not evidence
of the missing dependency. The successful CI installations above establish the
normal-install result independently.

## Proposed decision

Declare `numpy` and `pandas` as normal Crypto runtime requirements, preserving
the QPK source pin, package version, runtime imports and strategy implementation.
This assigns the immediate consumer responsibility for the import path it
retains. It does not change monitoring, execution or accounting behavior.

Use requirements without new numeric version bounds. The evidence establishes
dependency ownership, not a new supported minimum or a reason to upgrade a
consumer. In particular, copying the research extra's `pandas>=3.1.0` constraint
would exclude the existing [Binance Python 3.11 lock][binance-lock], which uses
NumPy 2.4.6 and Pandas 3.0.3. A separate normal-wheel CI case supplies those two
existing constraints to pip. Constraints restrict resolution; they do not
install otherwise undeclared packages. This case verifies installation and
entrypoint-import compatibility, not the complete consumer runtime.

Keep the `research` extra unchanged. Add normal dependency edges to Crypto's
root `uv.lock` record using the already locked NumPy and Pandas nodes, and add
the corresponding normal metadata entries. No resolved package version,
artifact, source revision, resolution marker or research edge changes. This is
a five-line root metadata update, not a lock regeneration.

### Existing research lock inconsistency

The baseline project requests `pandas>=3.1.0` for `research`, while the lock's
research metadata requests `>=3.0.5` and its Pandas node is 3.0.5. The mismatch
predates this proposal. Resolving it can change versions or the research
contract and is outside the authorized packaging declaration.

The QPK pin-consistency check validates shared Git references; it does not prove
general lock consistency or research-extra reproducibility. This proposal makes
no claim that the existing research lock is coherent. Reconciliation requires a
separate reviewed change and owner approval for any dependency-version change.

## Validation

The added normal-wheel CI jobs cover Python 3.11, Python 3.12, and Python 3.11
with the existing Binance numeric constraints. Each job:

1. Builds the normal wheel and installs it with its declared dependencies into a
   fresh virtual environment through the normal resolver.
2. Runs `pip check`, then imports `crypto_strategies.entrypoints` using the
   environment's isolated interpreter from outside the checkout.
3. Requires installed distribution metadata and module origins for Crypto, QPK,
   NumPy and Pandas. Crypto must come from a wheel, QPK must match the unchanged
   Git pin, and loaded modules must stay under that environment's site packages.
4. Fails on a missing import, incomplete metadata, a source or pin mismatch, an
   editable or contaminated environment, or any observed network attempt.

Offline tests cover the verifier's rejection paths. The previous CI jobs remain
intact, including strategy tests, Ruff, package build and shared-pin checks. No
existing skip or assertion is weakened. Runtime source is byte-for-byte
unchanged; normal installation and full repository CI must pass on the reviewed
proposal before it is considered ready for adoption review.

These checks cover the public entrypoint import and existing test suite. They do
not establish the dependency closure of every optional runtime module, exercise
real monitoring storage, or validate a deployed consumer.

## Alternatives

### Add a QPK monitoring extra

An explicit QPK extra would give the monitoring implementation ownership of its
requirements. The consumed QPK revision has no such extra, so this needs a
shared-package change and separately reviewed consumer adoption. It has a larger
release scope than correcting the immediate consumer declaration.

### Separate monitoring from strategy imports first

An explicit observation sink at application composition could remove the
monitoring dependency from evaluation imports. That requires characterization
of monitor reuse, persistence, record payloads, warning paths and call order.
It is a broader architecture change and should not be combined with this
packaging correction.

### Delay or suppress the import error

Moving the import into a best-effort recording function changes when a missing
dependency becomes visible. An existing broad exception handler could then
return a strategy decision without the expected observation. This does not
establish a complete installation or preserve the current failure boundary.

## Consequences, adoption and rollback

The declaration enlarges the normal installation and participates in consumer
dependency resolution. Green CI is packaging evidence; it does not authorize a
production dependency update. The proposal remains a draft until the owner
approves adoption. No production pin or deployment changes with this proposal.

If adopted and later reverted, restore the previous consumer package and lock
state through a reviewed revert. Keep the reproduction record: reverting the
declaration restores the known normal-install gap. Resolve the separate
research lock inconsistency without silently changing its contract.

[crypto-deps]: https://github.com/QuantStrategyLab/CryptoStrategies/blob/ec0b330081213f56c3ff98164ec00ba26c10dfc9/pyproject.toml#L11-L19
[crypto-common]: https://github.com/QuantStrategyLab/CryptoStrategies/blob/ec0b330081213f56c3ff98164ec00ba26c10dfc9/src/crypto_strategies/entrypoints/_common.py#L9-L14
[qpk-deps]: https://github.com/QuantStrategyLab/QuantPlatformKit/blob/c7646a7168b3dafa763ef7751a182d23e8de7790/pyproject.toml#L15-L25
[qpk-monitor]: https://github.com/QuantStrategyLab/QuantPlatformKit/blob/c7646a7168b3dafa763ef7751a182d23e8de7790/src/quant_platform_kit/strategy_lifecycle/performance_monitor.py#L17-L35
[qpk-metrics]: https://github.com/QuantStrategyLab/QuantPlatformKit/blob/c7646a7168b3dafa763ef7751a182d23e8de7790/src/quant_platform_kit/strategy_lifecycle/performance_metrics.py#L12-L13
[ci-install]: https://github.com/QuantStrategyLab/CryptoStrategies/blob/ec0b330081213f56c3ff98164ec00ba26c10dfc9/.github/workflows/ci.yml#L69-L78
[entrypoint-check]: https://github.com/QuantStrategyLab/CryptoStrategies/blob/ec0b330081213f56c3ff98164ec00ba26c10dfc9/tests/test_contract_governance.py#L96-L103
[baseline-pr]: https://github.com/QuantStrategyLab/CryptoStrategies/pull/299
[baseline-311]: https://github.com/QuantStrategyLab/CryptoStrategies/actions/runs/41202442005/job/124540746081
[baseline-312]: https://github.com/QuantStrategyLab/CryptoStrategies/actions/runs/41202442005/job/124540746036
[binance-lock]: https://github.com/QuantStrategyLab/BinancePlatform/blob/20209604a9288482bed9e25506d3c9b6942ea2f1/uv.lock
