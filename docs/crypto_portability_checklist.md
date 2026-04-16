# Crypto portability checklist

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
