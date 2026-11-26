# Contributing to the Hermes plugin

Read the [README](README.md) for host setup and the [public Tameru documentation](https://thewhitemage.gitbook.io/tameru-documentation/) for project contribution standards. Open a focused pull request against `main`. Keep changes to the engine in the engine repository and bring them into this plugin through its vendoring workflow.

Use a Python version supported by [pyproject.toml](pyproject.toml). In an isolated environment, run `python -m pip install -e '.[dev]'` and `python -m pytest -m 'not perf'`. Follow the [CI workflow](.github/workflows/ci.yml) for pinned-host and packaging checks. For changes to the host integration or packaged assets, record which host revision and installed package you exercised.

Include the test results and any deferred checks in the pull request. Follow [SECURITY.md](SECURITY.md) for vulnerability reports.
