# CryptoStrategies

[English README](README.md)

CryptoStrategies 保存 Binance 实盘交易所依赖的加密策略代码。它是 QuantStrategyLab 系统里的策略库层：BinancePlatform 从这里加载策略实现和元数据，而 live-pool 的选池、排序和晋级证据由 CryptoLivePoolPipelines、QuantPlatformKit 等上游流水线负责。这个包刻意保持精简——不持有券商凭据，也不包含部署开关。无论是实现或评审面向 Binance 的加密策略，还是确认某个 profile 是否已具备 live 交易资格，都可以从这个仓库入手。

> 投资有风险。本项目不构成投资建议，仅用于学习、研究和工程审阅。

## 这个仓库是什么

CryptoStrategies 是 QuantStrategyLab 的加密货币策略包。为 Binance 执行平台提供共享加密货币策略实现和运行元数据。

它属于一套多仓库量化系统中的一层：

- **策略包**：保存可复用策略代码、元数据和运行入口。
- **Snapshot 流水线**：生成官方 live-pool snapshot、ranking、回测和发布证据。
- **执行平台**：把策略接到券商、dry-run 检查、通知和 live 部署控制。
- **共享基础设施**：维护契约、配置、适配器、插件和审计 workflow，供多仓复用。

本仓库负责策略代码和元数据。对 snapshot-backed 加密策略来说，本仓库只消费上游 live pool，不在本地重建月度池成员或顺序。本仓库不保存券商凭据，不直接提交订单，也不替代 live enable 前需要看的 live-pool/release 证据。

## QSL 架构角色

- **层级**：`策略库`。
- **职责**：加密策略实现包。
- **事实源/归属**：BinancePlatform 消费的 runtime 策略代码和元数据。
- **消费对象**：QuantPlatformKit 和 CryptoLivePoolPipelines artifacts。
- **禁止事项**：持有券商凭据或执行部署。

## 策略 profile

### 普通 runtime 策略

这些 profile 可以基于 market history、portfolio snapshot 或其他运行时输入执行，不需要单独先生成 feature snapshot。

本策略包当前不暴露普通 runtime 策略。

### Snapshot-backed 策略

这些 profile 依赖 `CryptoLivePoolPipelines` 生成的 artifact；下游平台若要消费，应先确认对应产物已经验证。artifact 可用不等于已获 runtime allowlist / 晋级 / live 授权。

| Profile | 名称 | 说明 |
| --- | --- | --- |
| `crypto_live_pool_rotation` | 加密领涨轮动 | 目录状态为 `research_backtest_only` 的趋势轮动策略；以 CryptoLivePoolPipelines 发布的有序 live pool 为数据来源。兼容旧 profile `crypto_leader_rotation`；运行时代码可以在该池内做交易门控和仓位 sizing，但月度选池和排序属于上游。当前 runtime selectable allowlist 为空，**没有** live runtime grant 或晋级授权；消费上游 live-pool artifact 不等于已被 runtime 启用。 |

### 研究侧候选

研究侧 profile 可以保留在代码里用于复现和后续评审，但不应该出现在当前可配置 live profile 中。

| Profile | 名称 | 说明 |
| --- | --- | --- |
| `crypto_btc_dca` | Crypto BTC DCA | Shadow candidate，允许监控和定投复盘，但仍受平台门控。 |
| `crypto_trend_rotation` | Crypto Trend Rotation | 研究侧重构候选。 |
| `crypto_equity_combo` | Crypto Equity Combo | 研究侧编排候选。 |

独立的 `full_cycle_no_submit` 允许名单包含 `crypto_btc_dca`、
`crypto_trend_rotation` 和 `crypto_live_pool_rotation`，仅供有边界的验证使用，
不授予生产或 live 选择资格。平台验证仍须检查执行已禁用、账户隔离、必要输入和
券商提交阻断。独立趋势策略在 BTC 基准缺失或无效时拒绝生成目标。组合研究策略
保留其条件性的回退行为：提供映射形式的 benchmark 时，趋势腿失败仍可能保留
BTC 腿目标；未提供映射 benchmark 时，缺失 BTC 数据会在组合腿计算前报错。
因此，这项校验不能证明整个组合策略不产生订单意图。

独立趋势的运行入口要求权威 `universe_snapshot`，并要求 `derived_indicators`
包含完整的 BTC 日指标基准。BTC 基准及其别名、池外币种均不能成为轮动候选。
管理信息保留币池和平台投资组合快照提供的正数量持仓；它不新增退出规则，也不
证明同一账户内的跨策略持仓归属。直接研究调用可省略币池，并从指标行推导非 BTC
候选。没有显式 regime 时，该独立策略使用收盘价高于 SMA200 的条件，不套用
live-pool 的均线斜率规则。持仓可见性来自提供的投资组合，不来自行情价格键。
历史日指标生产者本身不能证明 K 线已收盘或满足 PIT；平台验证路径须单独建立这些
输入边界。

## 如何接到执行平台

执行平台通过 strategy loader 和 runtime metadata 消费本策略包。当前下游平台：BinancePlatform。

券商凭据、dry-run/live 开关、订单提交和部署配置都应放在执行平台仓库里，而不是放在策略仓库里。

## 策略证据和 live enablement

README 只作为项目地图，不替代最新表现数据。启用或调整 live profile 前，需要重新运行相关 live-pool/release pipeline，并分别看短、中、长周期的收益、最大回撤、相对基准收益、换手、数据新鲜度和 artifact 版本。月度 live-pool 选择、ranking 和 promotion 证据属于 CryptoLivePoolPipelines；本仓库的策略改动应保留这个上游权威边界。证据过期、不完整，或者 profile 仍标记为 research-only，就不要放进 live runtime settings。

## 月度报告审查

月度报告由 [CryptoLivePoolPipelines](https://github.com/QuantStrategyLab/CryptoLivePoolPipelines) 发布。它的 review-only 作业读取同一次运行的已发布月报及上游 selector/shadow 指标，各发起一次 OpenAI 和 Anthropic API 请求，单轮结果写入上游既有 `monthly-review` issue 的评论。CryptoStrategies 不重复运行月审，也不接受自动代码修改；上游指标不能证明下游策略收益或授权 live 提升。

默认每次输入上限 16,000 token、输出上限 2,500 token、超时 60 秒、零重试，UTC 自然月预算 1 美元，调用前通过 issue 评论预留费用。缺少既有 API Secret 时跳过 API 作业；材料缺失、预算不足或 API 失败时发布 `review unavailable`。配置、预算记录和人工审查入口见 [上游运行手册](https://github.com/QuantStrategyLab/CryptoLivePoolPipelines/blob/main/docs/operator-runbook.md#monthly-review-only-api-audit)。该流程不运行 Codex/Cursor CLI，不修改仓库或交易配置。

月报发布任务通过或 issue 已创建，只能证明材料已准备；审查完成需要评论中两家 API 的结果。修复建议必须经过 PR 和 CI。

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

- [`docs/crypto-cross-platform-strategy-spec.md`](docs/crypto-cross-platform-strategy-spec.md)
- [`docs/crypto-cross-platform-strategy-spec.zh-CN.md`](docs/crypto-cross-platform-strategy-spec.zh-CN.md)
- [`docs/crypto-portability-checklist.md`](docs/crypto-portability-checklist.md)
- [`docs/crypto-portability-checklist.zh-CN.md`](docs/crypto-portability-checklist.zh-CN.md)
- [`docs/crypto-strategy-template.md`](docs/crypto-strategy-template.md)
- [`docs/crypto-strategy-template.zh-CN.md`](docs/crypto-strategy-template.zh-CN.md)

## 安全和贡献说明

- AI 助手与维护者遵循 [AGENTS.md](AGENTS.md)。

- 不要把密钥、账户标识、token、Cookie 或券商凭据提交到 Git，也不要写进日志。
- 改动尽量小，并配套测试或可复现证据。
- 涉及策略行为的改动，请附上验证命令或产物路径。

## 社区和安全

- 贡献前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)，确认 PR 范围、本地校验和文档要求。
- 讨论、issue 和 review 请遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
- 涉及密钥、自动化、券商/交易所或云资源的漏洞请按 [SECURITY.md](SECURITY.md) 私密报告；不要为 secret 或实盘风险开公开 issue。

## 许可证

详见 [LICENSE](LICENSE)。
