# Publishing to PyPI

This guide covers how to release new versions of `ai-code-sessions` to PyPI.

---

## Prerequisites

- You have push access to the repository
- You have a PyPI account with upload permissions for this package
- `twine` is installed (`pipx install twine`)

---

## Release Process

### 0. Run Tests First

Before releasing, ensure all tests pass:

```bash
uv run --group dev pytest
```

### 1. Update the Version

Edit `pyproject.toml`:

```toml
[project]
version = "0.2.0"  # Bump this
```

Follow [Semantic Versioning](https://semver.org/):
- **MAJOR** (1.0.0): Breaking changes
- **MINOR** (0.2.0): New features, backward compatible
- **PATCH** (0.1.1): Bug fixes, backward compatible

### 2. Update Changelog (Optional)

If you maintain a CHANGELOG.md, add an entry for the new version.

### 3. Commit the Version Bump

```bash
git add pyproject.toml
git commit -m "chore: bump version to 0.2.0"
git push
```

### 4. Build Distributions

From the repository root:

```bash
# Clean and build
uv build --clear
```

This creates:
- `dist/ai_code_sessions-0.2.0.tar.gz` (sdist)
- `dist/ai_code_sessions-0.2.0-py3-none-any.whl` (wheel)

### 5. Upload to PyPI

Set credentials using a PyPI API token:

```bash
export TWINE_USERNAME="__token__"
export TWINE_PASSWORD="pypi-YOUR-API-TOKEN-HERE"
```

Upload:

```bash
twine upload dist/*
```

### 6. Verify Installation

```bash
# In a fresh environment
pipx install ai-code-sessions --force
ais --version
```

### 7. Tag the Release (Optional)

```bash
git tag v0.2.0
git push origin v0.2.0
```

---

## Testing with TestPyPI

Before publishing to production PyPI, test on TestPyPI:

### 1. Build

```bash
uv build --clear
```

### 2. Upload to TestPyPI

```bash
export TWINE_REPOSITORY_URL="https://test.pypi.org/legacy/"
export TWINE_USERNAME="__token__"
export TWINE_PASSWORD="pypi-YOUR-TESTPYPI-TOKEN-HERE"

twine upload dist/*
```

### 3. Install from TestPyPI

```bash
pipx install \
  --pip-args="--index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple" \
  ai-code-sessions
```

Note: `--extra-index-url https://pypi.org/simple` is needed because dependencies aren't on TestPyPI.

---

## Troubleshooting

### "File already exists"

You can't overwrite an existing version on PyPI. Bump the version number:

```toml
version = "0.2.1"  # Instead of 0.2.0
```

### "Invalid API token"

1. Verify the token is correct
2. Ensure the token has upload permission for this package
3. Check the token hasn't expired

### "twine: command not found"

```bash
pipx install twine
```

---

## API Token Setup

### Create a Token

1. Go to https://pypi.org/manage/account/token/
2. Create a token scoped to the `ai-code-sessions` project
3. Save it securely (you won't see it again)

### Using the Token

For one-time use:

```bash
export TWINE_USERNAME="__token__"
export TWINE_PASSWORD="pypi-YOUR-TOKEN"
twine upload dist/*
```

For persistent use, create `~/.pypirc`:

```ini
[pypi]
username = __token__
password = pypi-YOUR-TOKEN

[testpypi]
username = __token__
password = pypi-YOUR-TESTPYPI-TOKEN
```

Then:

```bash
twine upload dist/*                    # Uses [pypi]
twine upload -r testpypi dist/*        # Uses [testpypi]
```

---

## Checklist

Before releasing:

- [ ] All tests pass (`uv run --group dev pytest`)
- [ ] Version bumped in `pyproject.toml`
- [ ] Changes committed and pushed
- [ ] Built fresh distributions (`uv build --clear`)
- [ ] Tested on TestPyPI (for major releases)
- [ ] Uploaded to PyPI
- [ ] Verified installation works
- [ ] Tagged release in git (optional)
