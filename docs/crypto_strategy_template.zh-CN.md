# 加密策略模板

以后新增加密策略时，按这个模板落。

## 最小目录结构

至少会碰这些位置：

- `src/crypto_strategies/catalog.py`
- `src/crypto_strategies/manifests/__init__.py`
- `src/crypto_strategies/entrypoints/__init__.py`
- `src/crypto_strategies/runtime_adapters.py`
- `src/crypto_strategies/strategies/` 下面的一份实现模块
- `tests/` 下面对应测试

## 最小接入清单

1. 在 `src/crypto_strategies/catalog.py` 里加 `StrategyDefinition`
2. 在 `src/crypto_strategies/manifests/__init__.py` 里加 `StrategyManifest`
3. 在 `src/crypto_strategies/entrypoints/__init__.py` 里加统一 entrypoint
4. 在 `src/crypto_strategies/runtime_adapters.py` 里加 runtime adapter
5. 补 catalog 和 entrypoint 测试
6. 如果策略消费上游 artifact，补显式 `StrategyArtifactContract`
7. 如果要变成 live，再去下游平台补状态和 portability 检查

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

## Manifest 和 entrypoint

manifest 和 catalog 里的定义必须一致，至少包括：

- `profile`
- `domain`
- `display_name`
- `required_inputs`
- `default_config`

entrypoint 必须做到：

- 只从 `StrategyContext` 读取 canonical 输入
- 需要组合信息时读 `ctx.portfolio`
- 只返回 `StrategyDecision`
- 不把交易所专属执行细节放进策略仓

## Runtime adapter

新 adapter 至少要声明：

- `available_inputs`
- 如果策略会读 `ctx.portfolio`，则声明 `portfolio_input_name`
- 如果策略依赖上游 artifact，则声明 `artifact_contract`

如果某个平台还不支持这条策略，就不要先把它写进 `supported_platforms`。

artifact contract 属于策略包。平台仓库只负责把它映射到自己的环境变量、文件、Firestore/GCS 或 runtime 状态来源，不能靠 profile 名称分支来猜策略需要什么 artifact。

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

## 新策略 PR checklist

- [ ] 已新增带显式 `target_mode` 的 `StrategyDefinition`
- [ ] `StrategyManifest` 和 catalog 定义一致
- [ ] entrypoint 只读取 canonical 输入
- [ ] 每个兼容平台都补了 runtime adapter
- [ ] 需要上游 artifact 时已新增 artifact contract
- [ ] 策略代码里没有平台分支和环境变量读取
- [ ] catalog、entrypoint、governance 测试已更新
- [ ] 如果 rollout 变了，下游平台的状态脚本或 adapter smoke 也已更新
