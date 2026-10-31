# ADR 0002: Declare runtime monitoring dependencies

Status: proposed

## Context

A normal installation should provide the libraries needed to import its
supported runtime entrypoints. Research extras and the developer test
environment serve different purposes and should not supply an undeclared
requirement of that import path.

The inspected dependency declarations and sources expose a gap:

| Boundary | Evidence |
| --- | --- |
| Consumer metadata | [Crypto dependency declarations][crypto-deps] list QPK as the only normal dependency. NumPy and Pandas appear in the optional `research` extra. |
| Consumer import | [The common runtime entrypoint][crypto-common] imports QPK's `PerformanceMonitor` at module scope. |
| Consumed package metadata | [The pinned QPK declaration][qpk-deps] has an empty normal dependency list. NumPy and Pandas appear only in its `dev` extra. |
| Consumed import path | [The monitor][qpk-monitor] imports Pandas and its metrics module. [The metrics module][qpk-metrics] imports both NumPy and Pandas at module scope. |
| Existing verification | [CI installation][ci-install] explicitly installs NumPy and Pandas. [The catalog/entrypoint check][entrypoint-check] skips when the import reports missing Pandas. |

The QPK links identify the revision consumed by the referenced Crypto manifest,
rather than QPK's current default branch. This is static dependency and import
evidence. A failure in a clean wheel installation has not been reproduced by
this decision record, and no production failure is inferred. This path also
does not establish the complete dependency closure of every runtime module.

Passing tests in an environment that separately installs these libraries do
not answer whether the normal package declaration is sufficient. The existing
tests remain useful for the behavior they exercise.

## Proposed decision

First reproduce the normal-install import boundary in an isolated environment.
If the gap is confirmed, prefer the smallest packaging correction: explicitly
declare the libraries required by the retained runtime import path in Crypto's
normal dependencies, while preserving the QPK source pin and runtime code.
The observed path requires consideration of both Pandas and NumPy.

The supported version constraints must be established against the existing
consumer environments before that correction. Do not copy the research-extra
minimums into normal dependencies without compatibility evidence, broaden
constraints to make resolution succeed, or upgrade a platform implicitly.
Keep the existing `research` installation interface compatible when deciding
where its requirements belong.

This proposal selects no new dependency version and makes no change to package
metadata, locks, runtime imports, workflows or deployment configuration. A
dependency-adoption change remains separate from a behavior-preserving code
extraction.

## Alternatives

### Add a QPK monitoring extra

An explicit QPK extra would give the monitoring implementation ownership of its
requirements. Crypto would then depend on that extra. The currently consumed
QPK revision has no such extra, so this approach needs a shared-package change
and a separately reviewed consumer adoption. It has a larger release scope than
correcting the immediate consumer declaration.

### Separate monitoring from strategy imports first

An explicit observation sink at application composition can keep evaluation
imports independent of monitoring infrastructure. That requires
characterization of monitor reuse, record payloads, persistence, warning paths
and call order. It is a broader architecture change and should not be combined
with the packaging correction.

### Delay or suppress the import error

Moving the import into a best-effort recording function changes when a missing
dependency becomes visible. If the existing broad exception handler then
continues, an import failure could become a returned strategy decision without
the expected observation. This does not establish a complete installation and
must not be presented as a behavior-preserving fix.

## Validation required before implementation

1. Build and install the baseline wheel in a fresh environment using only its
   declared normal dependencies. Keep research and development extras absent;
   do not add NumPy or Pandas through test setup.
2. Import the supported runtime entrypoint without a sibling source checkout.
   Record the actual imported module paths and installed distribution metadata.
   Separate an import failure from a resolver, network or build failure.
3. Test the proposed declaration against the same boundary and the supported
   consumer constraints. Keep the QPK source revision fixed. Run dependency
   consistency checks without silently changing the consumer's lock file.
4. Run the affected strategy and monitoring tests with synthetic inputs and
   controlled storage substitutes. Compare decisions, exceptions, observation
   payloads, call order and warning behavior. No broker or real storage access
   is needed for this packaging validation.
5. Retain the existing checks. A normal-install smoke check must fail on a
   missing required import; a skip is not evidence that the installation works.
   Verify that the research extra remains installable and compatible.

The current metadata, import path and CI setup can be inspected independently
of this proposed validation. A successful documentation check does not complete
the clean-install experiment.

## Consequences and rollback

A declaration correction preserves the current runtime code and makes its
installation requirements explicit. It can enlarge the normal installation and
affect dependency resolution, so it still requires consumer compatibility
review. It does not remove monitoring's storage-dependent side effects.

The alternative import-boundary change may reduce ordinary runtime
dependencies, but it must preserve observable monitoring behavior or be treated
as a separate behavior change.

This proposed record requires no migration. If a later packaging change is
adopted, revert that consumer adoption separately to the previously supported
package and lock state. Retain the previous implementation and its compatibility
evidence; do not rewrite published history or hide the unresolved import gap.

## Open decision

Choose whether to proceed with a normal dependency declaration, develop a QPK
monitoring extra for separate adoption, or first isolate monitoring from the
strategy import path. The proposed first choice is the normal declaration,
conditional on the clean-install result and an agreed compatible constraint
set. Neither dependency adoption nor the alternative architecture change is
implemented by this record.

[crypto-deps]: https://github.com/QuantStrategyLab/CryptoStrategies/blob/b537248094f3cd123b1400c421a4bb89c71b1981/pyproject.toml#L11-L19
[crypto-common]: https://github.com/QuantStrategyLab/CryptoStrategies/blob/b537248094f3cd123b1400c421a4bb89c71b1981/src/crypto_strategies/entrypoints/_common.py#L9-L14
[qpk-deps]: https://github.com/QuantStrategyLab/QuantPlatformKit/blob/c7646a7168b3dafa763ef7751a182d23e8de7790/pyproject.toml#L15-L25
[qpk-monitor]: https://github.com/QuantStrategyLab/QuantPlatformKit/blob/c7646a7168b3dafa763ef7751a182d23e8de7790/src/quant_platform_kit/strategy_lifecycle/performance_monitor.py#L17-L35
[qpk-metrics]: https://github.com/QuantStrategyLab/QuantPlatformKit/blob/c7646a7168b3dafa763ef7751a182d23e8de7790/src/quant_platform_kit/strategy_lifecycle/performance_metrics.py#L12-L13
[ci-install]: https://github.com/QuantStrategyLab/CryptoStrategies/blob/b537248094f3cd123b1400c421a4bb89c71b1981/.github/workflows/ci.yml#L69-L78
[entrypoint-check]: https://github.com/QuantStrategyLab/CryptoStrategies/blob/b537248094f3cd123b1400c421a4bb89c71b1981/tests/test_contract_governance.py#L96-L103
