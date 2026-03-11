# Publishing To PyPI

This project publishes to TestPyPI and PyPI with `uv`, `twine`, and 1Password CLI (`op`).

## Prerequisites

- `op` is installed and signed in
- Desktop integration is enabled for 1Password CLI
- Local env files exist at:
  - `~/Library/Application Support/ai-code-sessions/twine-testpypi.env`
  - `~/Library/Application Support/ai-code-sessions/twine-pypi.env`
- The working tree is clean

Each env file should define:

```dotenv
TWINE_USERNAME=__token__
TWINE_PASSWORD=op://<Vault>/<Item>/API Token
TWINE_NON_INTERACTIVE=1
```

## Release Checklist

1. Confirm the version in `pyproject.toml`.
2. Run verification:

   ```bash
   uv run --group dev pytest
   uv run --group dev ruff format --check .
   uv run --group dev ruff check .
   ```

3. Confirm the version does not already exist on TestPyPI or PyPI.
4. Build into a fresh `.tmp/` directory:

   ```bash
   VERSION="$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
   OUT_DIR=".tmp/dist-${VERSION}-$(date +%Y%m%d-%H%M%S)"
   uv build --out-dir "$OUT_DIR"
   twine check "$OUT_DIR"/*
   ```

5. Upload to TestPyPI:

   ```bash
   OP_ACCOUNT="${OP_ACCOUNT:-f3g.1password.com}"
   op --account "$OP_ACCOUNT" run --env-file "$HOME/Library/Application Support/ai-code-sessions/twine-testpypi.env" -- \
     twine upload --repository-url https://test.pypi.org/legacy/ "$OUT_DIR"/*
   ```

6. Smoke-test the published package from TestPyPI:

   ```bash
   VENV_DIR=".tmp/pypi-smoke-${VERSION}-$(date +%Y%m%d-%H%M%S)"
   python3 -m venv "$VENV_DIR"
   source "$VENV_DIR/bin/activate"
   pip install --index-url https://test.pypi.org/simple --extra-index-url https://pypi.org/simple "ai-code-sessions==${VERSION}"
   ais --version
   ai-code-sessions --version
   ```

7. Upload to PyPI after explicit confirmation:

   ```bash
   OP_ACCOUNT="${OP_ACCOUNT:-f3g.1password.com}"
   op --account "$OP_ACCOUNT" run --env-file "$HOME/Library/Application Support/ai-code-sessions/twine-pypi.env" -- \
     twine upload "$OUT_DIR"/*
   ```

8. Smoke-test the PyPI release.
9. Move the build and smoke-test artifacts from `.tmp/` into `.archive/`.

## Notes

- Never print secrets or env-file contents.
- Prefer `op run --env-file ... -- <command>` so credentials only exist for the subprocess.
- If the version already exists on either registry, bump it before building.
