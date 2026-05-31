# Crypto portability checklist


## 中文摘要

- 完整中文版见 [`crypto_portability_checklist.zh-CN.md`](crypto_portability_checklist.zh-CN.md)；本节保留在英文文件顶部，方便从当前文件直接找到中文入口。
- 用途：本文档围绕 `Crypto portability checklist`，用于理解 `CryptoStrategies` 的配置、运行、部署、研究或验收边界。
- 主要覆盖：`Crypto portability checklist`。
- 阅读顺序：先确认边界、输入输出和权限要求，再执行文档里的命令、CI、dry-run、发布或切换步骤。
- 风险提示：涉及实盘、密钥、权限、Cloud Run、交易所或券商 API 的变更，必须先在测试环境或 dry-run 验证；不要只凭示例直接修改生产。
- 英文正文保留更完整的命令、字段名和配置键；如果摘要和正文不一致，以正文中的实际命令和配置为准。
Use this before enabling a crypto profile on any downstream platform.

- [ ] `required_inputs` only use canonical crypto input names
- [ ] `target_mode` is explicitly declared
- [ ] every compatible platform has a runtime adapter
- [ ] upstream artifact needs are declared through `artifact_contract`, not platform profile branches
- [ ] strategy code does not branch on platform names
- [ ] strategy code does not read exchange env vars
- [ ] downstream runtime builds canonical inputs, not exchange-shaped names
- [ ] platform README or status script reflects the actual enabled profile set
- [ ] tests cover catalog, entrypoint, adapter smoke, and status reporting
