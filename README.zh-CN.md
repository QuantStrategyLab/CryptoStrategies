# CryptoStrategies

[English README](README.md)

> 投资有风险。本项目不构成投资建议，仅用于学习、研究和工程审阅。

## 这个仓库是什么

CryptoStrategies 是 QuantStrategyLab 的加密货币策略包。为 Binance 执行平台提供共享加密货币策略实现和运行元数据。

它属于一套多仓库量化系统中的一层：

- **策略包**：保存可复用策略代码、元数据和运行入口。
- **Snapshot 流水线**：生成官方 live-pool snapshot、ranking、回测和发布证据。
- **执行平台**：把策略接到券商、dry-run 检查、通知和 live 部署控制。
- **共享基础设施**：沉淀契约、配置、适配器、插件和审计 workflow，供多仓复用。

本仓库负责策略代码和元数据。对 snapshot-backed 加密策略来说，本仓库只消费上游 live pool，不在本地重建月度池成员或顺序。本仓库不保存券商凭据，不直接提交订单，也不替代 live enable 前需要看的 live-pool/release 证据。

## 策略 profile

### 普通 runtime 策略

这些 profile 可以基于 market history、portfolio snapshot 或其他运行时输入执行，不需要单独先生成 feature snapshot。

本策略包当前不暴露普通 runtime 策略。

### Snapshot-backed 策略

这些 profile 依赖 `CryptoLivePoolPipelines` 生成的 artifact；下游平台使用前，应先确认对应产物已经验证和提升。

| Profile | 名称 | 说明 |
| --- | --- | --- |
| `crypto_leader_rotation` | Crypto Leader Rotation | 消费 CryptoLivePoolPipelines 发布的有序 live pool 的趋势轮动策略。运行时代码可以在该池内做交易门控和仓位 sizing，但月度选池和排序属于上游。 |

### 研究侧候选

研究侧 profile 可以保留在代码里用于复现和后续评审，但不应该出现在当前可配置 live profile 中。

本策略包当前不暴露普通 runtime 策略。

## 如何接到执行平台

执行平台通过 strategy loader 和 runtime metadata 消费本策略包。当前下游平台：BinancePlatform。

券商凭据、dry-run/live 开关、订单提交和部署配置都应放在执行平台仓库里，而不是放在策略仓库里。

## 策略证据和 live enablement

README 只作为项目地图，不替代最新表现数据。启用或调整 live profile 前，需要重新运行相关 live-pool/release pipeline，并分别看短、中、长周期的收益、最大回撤、相对基准收益、换手、数据新鲜度和 artifact 版本。月度 live-pool 选择、ranking 和 promotion 证据属于 CryptoLivePoolPipelines；本仓库的策略改动应保留这个上游权威边界。证据过期、不完整，或者 profile 仍标记为 research-only，就不要放进 live runtime settings。

## 仓库结构

- `src/`：库代码和运行时代码。
- `tests/`：单元测试、契约测试和回归测试。
- `docs/`：运行手册、设计说明、证据和集成契约。
- `.github/workflows/`：CI、定时任务、发布或部署 workflow。

## 快速开始

```bash
python -m pip install -e .
python -m pytest -q
```

## 延伸文档

- [`docs/crypto_cross_platform_strategy_spec.md`](docs/crypto_cross_platform_strategy_spec.md)
- [`docs/crypto_cross_platform_strategy_spec.zh-CN.md`](docs/crypto_cross_platform_strategy_spec.zh-CN.md)
- [`docs/crypto_portability_checklist.md`](docs/crypto_portability_checklist.md)
- [`docs/crypto_portability_checklist.zh-CN.md`](docs/crypto_portability_checklist.zh-CN.md)
- [`docs/crypto_strategy_template.md`](docs/crypto_strategy_template.md)
- [`docs/crypto_strategy_template.zh-CN.md`](docs/crypto_strategy_template.zh-CN.md)

## 安全和贡献说明

- 不要把密钥、账户标识、token、Cookie 或券商凭据提交到 Git，也不要写进日志。
- 改动尽量小，并配套测试或可复现证据。
- 涉及策略行为的改动，请附上验证命令或产物路径。

## 社区和安全

- 贡献前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)，确认 PR 范围、本地校验和文档要求。
- 讨论、issue 和 review 请遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
- 涉及密钥、自动化、券商/交易所或云资源的漏洞请按 [SECURITY.md](SECURITY.md) 私密报告；不要为 secret 或实盘风险开公开 issue。

## 许可证

详见 [LICENSE](LICENSE)。
