# Crypto combo 历史收益口径修正（2026-10-03）

`crypto_combo_backtest_20260628.json` 是旧版资金流口径的只读历史结果。它把每日 DCA 入金混入账户权益百分比变化，因此其中的年化收益、Sharpe、回撤和总收益均不代表投资绩效；本说明不覆盖或重写该 artifact。

当前 Crypto combo runner 以现金流调整后的单位净值收益计算 TWR、CAGR、Sharpe 和回撤，并另外报告 XIRR、期初/期末权益、累计注资、累计提现及净利润。该研究代理明确假设每笔 DCA 在当日收盘时到达，并按同一收盘价买入；窗口化指标使用窗口前一日的实际权益作为期初资本。现金流计时必须在同一评估序列中保持一致，TWR 依现金流时点切分并链乘。

XIRR 将同一自然日的现金流先合并并移除净额为零的日期。若净现金流存在多次正负号变化，XIRR 标为歧义并留空，不从数值搜索找到的单个候选根推断唯一解；该搜索不保证找到全部根。这样可避免非传统现金流因网格漏检而输出看似唯一的回报率。

combo 中的山寨币仍是由 ETH 收益缩放并加噪声生成的 synthetic proxy。它不是实际策略重放，也不提供真实成交、结算或成本证据。当前 proxy 未建模费用与滑点，结果显式标记 `cost_status=not_modelled`；为保持既有消费者数值兼容而输出的零成本数值不代表已测量成本。旧 artifact 没有在本次修正中重跑，也不得用于晋级或证明策略效果。

现金流调整与绩效报告口径参照 [GIPS Standards Handbook for Firms](https://www.gipsstandards.org/standards/gips-standards-for-firms/gips-standards-handbook-for-firms/)；此处仅声明本模拟器的现金流时间约定，不代表 GIPS 合规认证。
