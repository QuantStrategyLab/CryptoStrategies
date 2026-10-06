# Contributing

Thanks for contributing to `CryptoStrategies`.

## Ground Rules

- Prefer small, low-risk pull requests.
- Keep refactors separate from behavior changes.
- Add or update tests when changing runtime behavior.
- Do not use deployment or scheduled workflows as a substitute for local verification.
- Changes touching live trading, credentials, permissions, Cloud Run, or exchange/broker APIs must be verified in a test environment or dry run first; do not modify production based on the examples alone.

## Branching and Pull Requests

- Create a topic branch for each change.
- Open a pull request with a short summary and a concrete test plan.
- Wait for CI to pass before merging.

## Local Verification

Run the main verification command before opening a pull request:

```bash
python3 -m pip install -e . numpy pandas pytest pytest-cov ruff build \
  && python3 -m pip install --no-deps -e ../QuantPlatformKit \
  && python3 -m pip check \
  && ruff check . \
  && python3 -m pytest -q tests --cov --cov-report=term --cov-report=xml \
  && python3 -m build
```
