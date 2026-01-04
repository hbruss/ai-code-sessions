# Publishing to PyPI

This project is intended to be installed via `pipx`:

```bash
pipx install ai-code-sessions
```

## 1) Bump the version

Update `pyproject.toml`:

- `[project].version = "…"`

## 2) Build distributions

From the repo root:

```bash
uv build --clear
```

Artifacts are written to `dist/`:

- `dist/*.tar.gz` (sdist)
- `dist/*.whl` (wheel)

## 3) Upload

Install `twine` (one-time):

```bash
pipx install twine
```

Set credentials using a PyPI API token:

```bash
export TWINE_USERNAME="__token__"
export TWINE_PASSWORD="pypi-REDACTED"
```

Upload:

```bash
twine upload dist/*
```

## Optional: TestPyPI first

```bash
export TWINE_REPOSITORY_URL="https://test.pypi.org/legacy/"
twine upload dist/*
```

Then install from TestPyPI (example):

```bash
pipx install --pip-args="--index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple" ai-code-sessions
```
