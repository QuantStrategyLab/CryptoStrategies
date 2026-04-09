# 加密策略模板

以后新增加密策略时，按这个模板落。

## 最小接入清单

1. 在 `src/crypto_strategies/catalog.py` 里加 `StrategyDefinition`
2. 在 `src/crypto_strategies/manifests/__init__.py` 里加 `StrategyManifest`
3. 在 `src/crypto_strategies/entrypoints/__init__.py` 里加统一 entrypoint
4. 在 `src/crypto_strategies/runtime_adapters.py` 里加 runtime adapter
5. 补 catalog 和 entrypoint 测试
6. 如果要变成 live，再去下游平台补状态和 portability 检查

## StrategyDefinition 必填项

每条新策略都必须声明：

- `profile`
- `domain="crypto"`
- `supported_platforms`
- `entrypoint`
- `required_inputs`
- `default_config`
- `target_mode`

其中 `required_inputs` 只能用 canonical 名。

## Runtime adapter

新 adapter 至少要声明：

- `available_inputs`
- 如果策略会读 `ctx.portfolio`，则声明 `portfolio_input_name`

如果某个平台还不支持这条策略，就不要先把它写进 `supported_platforms`。

## 禁止的偷懒方式

不要：

- 在策略代码里按平台分支
- 在策略代码里读交易所环境变量
- 直接返回 Binance 专属下单字段
- 让下游运行时继续绑定内部 helper 模块名

## 最低测试要求

- catalog test
- entrypoint test
- 检查 canonical inputs 和显式 target mode 的治理测试
- 变成 live 前，下游平台要补 adapter smoke test
