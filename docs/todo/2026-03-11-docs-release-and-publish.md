# Docs, Release, And Publish Checklist

- [x] Audit remaining end-user docs for stale `ais setup` language
- [x] Patch docs to explain onboarding wizard and manual skill-install flow
- [x] Re-run verification (`pytest`, `ruff check`, `ruff format --check`)
- [x] Request final code review on the release-ready diff
- [x] Confirm release version and publish targets
- [ ] Commit release changes
- [ ] Push to `main`
- [ ] Build distribution artifacts
- [ ] Publish to TestPyPI and/or PyPI
- [ ] Run install smoke test against the published version
- [ ] Move build and smoke-test artifacts from `.tmp/` to `.archive/`
