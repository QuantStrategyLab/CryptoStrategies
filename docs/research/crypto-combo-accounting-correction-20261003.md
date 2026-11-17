# Crypto combo 历史收益口径修正（2026-10-03）

`crypto_combo_backtest_20260628.json` 是旧版资金流口径的只读历史结果。它把每日 DCA 入金混入账户权益百分比变化，因此其中的年化收益、Sharpe、回撤和总收益均不代表投资绩效；本说明不覆盖或重写该 artifact。

当前 Crypto combo runner 以现金流调整后的单位净值收益计算 TWR、CAGR、Sharpe 和回撤，并另外报告 XIRR、期初/期末权益、累计注资、累计提现及净利润。该研究代理明确假设每笔 DCA 在当日收盘时到达，并按同一收盘价买入；窗口化指标使用窗口前一日的实际权益作为期初资本。现金流计时必须在同一评估序列中保持一致，TWR 依现金流时点切分并链乘。

XIRR 将同一自然日的现金流先合并并移除净额为零的日期。若净现金流存在多次正负号变化，XIRR 标为歧义并留空，不从数值搜索找到的单个候选根推断唯一解；该搜索不保证找到全部根。这样可避免非传统现金流因网格漏检而输出看似唯一的回报率。

combo 中的山寨币仍是由 ETH 收益缩放并加噪声生成的 synthetic proxy。它不是实际策略重放，也不提供真实成交、结算或成本证据。当前 proxy 未建模费用与滑点，结果显式标记 `cost_status=not_modelled`；为保持既有消费者数值兼容而输出的零成本数值不代表已测量成本。旧 artifact 没有在本次修正中重跑，也不得用于晋级或证明策略效果。

另有独立 `backtest.combo_replay` 研究入口，直接调用真实 `crypto_equity_combo.build_target_weights`，消费调用方提供并带 as-of/available-at 的 indicators、universe、benchmark 快照及 OHLC；它不生成 ETH 收益代理。信号只在 t 日收盘形成，t+1 自然日开盘按 `_rebalance_holdings` 调仓，随后以 t+1 收盘价估值；策略组合权益来自信号日收盘时已知的现金和持仓。显式 `fee_bps` / `slippage_bps` 是合成执行假设，按成交名义金额从现金扣除，并非测得的实际成本；本入口不支持外部现金流。当前工程案例仅用 synthetic 输入验证时序与账本，不构成策略表现、晋级或 live 证据。

现金流调整与绩效报告口径参照 [GIPS Standards Handbook for Firms](https://www.gipsstandards.org/standards/gips-standards-for-firms/gips-standards-handbook-for-firms/)；此处仅声明本模拟器的现金流时间约定，不代表 GIPS 合规认证。
