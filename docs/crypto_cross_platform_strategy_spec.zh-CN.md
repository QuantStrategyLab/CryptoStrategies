# 加密策略跨平台规范

这个仓库现在按照美股那套边界来拆：

- `CryptoStrategies` 负责纯策略逻辑、manifest、元数据和 runtime adapter
- 下游平台负责交易所接入、行情采集、artifact 新鲜度校验、下单和通知
- 策略代码只能读 `StrategyContext`
- 平台专属拼装逻辑必须留在平台仓库

## canonical 输入集合

以后新增加密策略时，`required_inputs` 只能从下面这组里选：

- `market_prices`
- `derived_indicators`
- `benchmark_snapshot`
- `portfolio_snapshot`
- `universe_snapshot`

当前 live 策略里的含义是：

- `market_prices`：按 symbol 组织的最新可交易价格
- `derived_indicators`：按 symbol 组织的策略级趋势指标
- `benchmark_snapshot`：基准状态快照，当前是 BTC
- `portfolio_snapshot`：与交易所无关的组合和现金快照
- `universe_snapshot`：本轮候选可交易标的集合

## target mode

每个加密策略都必须显式声明一个 `target_mode`。

当前默认是：

- `crypto_leader_rotation` -> `weight`

如果以后某个平台原生不吃 `weight`，只能在 runtime 边界做翻译，不能把平台分支写进策略代码。

## runtime adapter

每个兼容平台都必须给每条策略配 runtime adapter。

至少要声明：

- `available_inputs`
- `available_capabilities`
- 当策略需要 `ctx.portfolio` 时的 `portfolio_input_name`

## 允许和禁止

策略代码允许做的事：

- 从 `ctx.market_data` 读取 canonical 输入
- 读取 `ctx.portfolio`
- 从 `ctx.runtime_config` 读取纯策略参数
- 返回 `StrategyDecision`

策略代码禁止做的事：

- 写 Binance 或未来其他平台分支
- 直接读环境变量
- 直接输出交易所专属下单字段
- 直接查 artifact 路径或做 artifact 新鲜度校验

## 当前落地状态

现在只有一条 live profile：

- `crypto_leader_rotation`

现在也只有一个平台 adapter：

- `binance`

但契约已经按多策略、多平台形态写好，后面新增 crypto 策略时不需要再绑死 Binance 专属输入名。
