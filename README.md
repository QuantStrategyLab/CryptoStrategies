# CryptoStrategies

[English](#english) | [中文](#中文)

---

<a id="english"></a>
## English

Standalone crypto strategy repository for QuantStrategyLab platforms.

This repository owns pure strategy logic and strategy metadata. The downstream execution repo still owns exchange access, market-data fetches, runtime state, circuit breakers, Flexible Earn handling, notifications, and order placement.

### Contract boundary

The supported downstream surface is now the manifest-backed unified entrypoint for each live profile.

- `CryptoStrategies` owns pure decision logic and manifest metadata
- `BinancePlatform` loads that entrypoint through `QuantPlatformKit`
- shared outputs stay inside `StrategyDecision`
- exchange-specific safety checks, order sequencing, and artifact freshness handling stay in the execution repo

Legacy `core` / `rotation` modules may still exist as internal implementation details, but downstream runtimes should not bind to those component names anymore.

### Strategy index

| Profile | Downstream runtime today | Core idea |
| --- | --- | --- |
| `crypto_leader_rotation` | `BinancePlatform` | BTC core budget plus monthly altcoin leader rotation |

These strategies are consumed by platform repositories through `QuantPlatformKit` strategy contracts and component loaders.

### crypto_leader_rotation

**Objective**
- Keep BTC as the core asset while giving the non-BTC sleeve to a selective trend-following rotation.
- Avoid holding a broad alt basket all the time; only deploy into names that are strong both absolutely and versus BTC.

**Repository boundary**
- This repository owns:
  - BTC target-ratio and base-order budgeting helpers
  - trend-pool ranking and monthly refresh / lock logic
  - candidate selection, inverse-vol weighting, and sell-reason rules
- `BinancePlatform` currently owns:
  - AHR999 and Z-Score data fetches
  - DCA buy / trim execution
  - exchange safety checks, balance handling, and circuit breaker behavior
  - Flexible Earn subscribe / redeem flow and Telegram notifications

**BTC core budget logic**
- Target BTC weight grows with equity:
  - `btc_target_ratio = 0.14 + 0.16 * ln(1 + total_equity / 10000)`
  - capped at `65%`
- Base daily BTC order size is:
  - `max(15 USDT, total_equity × 0.0012)`
- `compute_allocation_budgets(...)` splits available USDT between:
  - the trend sleeve (`trend_usdt_pool`)
  - the BTC accumulation sleeve (`dca_usdt_pool`)
- This lets the downstream executor size BTC accumulation and trend allocation from one equity-aware budget framework.

**Current live BTC execution rules in BinancePlatform**
- `AHR999 < 0.45` → buy multiplier `5x`
- `0.45 <= AHR999 < 0.8` → buy multiplier `2x`
- `0.8 <= AHR999 < 1.2` → buy multiplier `1x`
- `AHR999 >= 1.2` → no scheduled BTC buy
- If `Z-Score > sell_trigger`, the runtime trims BTC.
- Trim size is currently `10%`, `30%`, or `50%` as the overvaluation rises (`trigger`, `>4`, `>5`).

**Trend-pool construction**
- The live stack prefers an upstream published monthly pool, but this repository also contains the internal ranking logic used to rebuild or validate that pool.
- Current live Binance defaults:
  - pool size `5`
  - minimum history `365` days
  - minimum `180d` average quote volume `8,000,000`
  - existing-pool membership bonus `0.10`
- Ranking factors include:
  - trend quality (`price vs SMA20 / 60 / 200`)
  - persistence
  - liquidity and liquidity stability
  - relative strength vs BTC
  - risk-adjusted momentum
- The pool score is a weighted sum of normalized ranks, with a small bonus for names already in the previous pool.

**Rotation-entry rules**
- The BTC regime gate must be on.
- A candidate must be above `SMA20`, `SMA60`, and `SMA200`.
- Relative strength vs BTC must be positive.
- Absolute momentum (`0.5×ROC20 + 0.3×ROC60 + 0.2×ROC120`) must also be positive.
- The top `2` candidates are selected by relative score.
- Default weighting is inverse volatility, so lower-vol winners receive slightly more capital.

**Exit and defense rules**
- A held symbol can be sold for three reasons:
  - it rotated out of the selected top names
  - price fell below `SMA60`
  - price broke the ATR trailing stop: `highest_price - ATR_MULTIPLIER × ATR14`
- The current live Binance profile uses `ATR_MULTIPLIER = 2.5`.
- Pool membership is locked by upstream `version` / `as_of_date` state so the live pool does not churn mid-month unless a refresh is intended.

---

<a id="中文"></a>
## 中文

这是 `QuantStrategyLab` 的独立加密货币策略仓。

这个仓库负责纯策略逻辑和策略元数据。下游执行仓库继续负责交易所接入、行情获取、运行时状态、熔断、Flexible Earn、通知和实际下单。

### 契约边界

当前正式对下游开放的是每个 live profile 的 manifest 驱动统一 entrypoint。

- `CryptoStrategies` 负责纯决策逻辑和 manifest 元数据
- `BinancePlatform` 通过 `QuantPlatformKit` 加载这个 entrypoint
- 共享输出保持在 `StrategyDecision` 契约内
- 交易所专属安全检查、下单顺序和 artifact 新鲜度校验继续放在执行仓库

旧的 `core` / `rotation` 模块可以继续作为仓库内部实现细节存在，但下游运行时不应再绑定这些组件名。

### 策略索引

| 策略档位 | 当前下游运行仓库 | 核心思路 |
| --- | --- | --- |
| `crypto_leader_rotation` | `BinancePlatform` | 以 BTC 为核心仓，再叠加月度山寨币强者轮动 |

这些策略通过 `QuantPlatformKit` 提供的策略契约和组件加载接口，被各个平台仓库引用。

### crypto_leader_rotation

**策略目标**
- 让 BTC 继续作为核心资产，同时把非 BTC 仓位交给一套有筛选的趋势轮动。
- 不长期被动持有一篮子山寨币，只把资金部署到绝对趋势和相对 BTC 强度都过关的标的上。

**仓库边界**
- 这个仓库负责：
  - BTC 目标仓位和基础下单预算的计算
  - 趋势池打分、月度刷新和锁定逻辑
  - 候选币筛选、逆波动率权重、卖出原因判断
- 当前 `BinancePlatform` 负责：
  - `AHR999` 和 `Z-Score` 数据获取
  - BTC 定投 / 分档止盈执行
  - 交易所安全检查、余额处理和熔断
  - Flexible Earn 申购 / 赎回，以及 Telegram 通知

**BTC 核心仓预算逻辑**
- BTC 目标权重会随总权益增长：
  - `btc_target_ratio = 0.14 + 0.16 * ln(1 + total_equity / 10000)`
  - 上限 `65%`
- BTC 每日基础下单额是：
  - `max(15 USDT, total_equity × 0.0012)`
- `compute_allocation_budgets(...)` 会把可用 USDT 拆成：
  - 趋势层预算 `trend_usdt_pool`
  - BTC 累积预算 `dca_usdt_pool`
- 这样下游执行层就能在同一个按权益变化的预算框架里，同时管理 BTC 核心仓和趋势层。

**当前 Binance live 执行层里的 BTC 规则**
- `AHR999 < 0.45` → 买入倍率 `5x`
- `0.45 <= AHR999 < 0.8` → 买入倍率 `2x`
- `0.8 <= AHR999 < 1.2` → 买入倍率 `1x`
- `AHR999 >= 1.2` → 当轮不做计划内 BTC 买入
- 当 `Z-Score > sell_trigger` 时，运行层会触发 BTC 分档止盈。
- 当前止盈比例是 `10% / 30% / 50%` 三档，对应高估程度继续抬升（`trigger`、`>4`、`>5`）。

**趋势池构建**
- live 链路优先消费上游发布的月度池，但这个仓库也保留了内部打分逻辑，用于重建或校验该池。
- 当前 Binance live 默认参数：
  - 池大小 `5`
  - 最少历史数据 `365` 天
  - `180 日`平均成交额下限 `8,000,000`
  - 上月已入池标的加分 `0.10`
- 打分因子包括：
  - 趋势质量（`price vs SMA20 / 60 / 200`）
  - 趋势持续性
  - 流动性和流动性稳定度
  - 相对 BTC 强度
  - 风险调整后动量
- 最终分数是各个归一化 rank 的加权和，再叠加一小段旧池成员加分。

**趋势层入场规则**
- 必须先满足 BTC 闸门开启。
- 候选币必须站上 `SMA20`、`SMA60`、`SMA200`。
- 相对 BTC 强度必须为正。
- 绝对动量 `0.5×ROC20 + 0.3×ROC60 + 0.2×ROC120` 也必须为正。
- 按相对得分选出前 `2` 名。
- 默认用逆波动率分配权重，所以波动更低的赢家会拿到略高一点的资金。

**退出和防守规则**
- 已持有的币会因为 3 类原因卖出：
  - 已经轮出当前 Top 名单
  - 价格跌破 `SMA60`
  - 价格跌破 ATR 跟踪止损：`highest_price - ATR_MULTIPLIER × ATR14`
- 当前 Binance live profile 使用 `ATR_MULTIPLIER = 2.5`。
- 趋势池会按上游 `version / as_of_date` 做锁定，避免在月中因为偶发刷新造成 live 池频繁抖动。
