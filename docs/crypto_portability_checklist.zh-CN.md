# 加密策略 portability 清单

在某条加密策略真正放到下游平台启用前，先过这张清单。

- [ ] `required_inputs` 只使用 canonical 加密输入名
- [ ] `target_mode` 已显式声明
- [ ] 每个兼容平台都有对应 runtime adapter
- [ ] 上游 artifact 需求通过 `artifact_contract` 声明，而不是下游平台 profile 分支
- [ ] 策略代码没有平台分支
- [ ] 策略代码没有直接读取交易所环境变量
- [ ] 下游 runtime 传给策略的是 canonical 输入，而不是交易所专属名字
- [ ] 平台 README 或状态脚本已经反映真实启用策略
- [ ] 测试覆盖了 catalog、entrypoint、adapter smoke 和状态输出
