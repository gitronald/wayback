# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-07-24

### Added

- `get_url_snapshots`: collect Wayback Machine CDX snapshots for a URL into a DataFrame
- `get_url_list_snapshots`: batch-collect snapshots for a list of URLs into per-URL CSV files
- Packaging via `pyproject.toml` (hatchling build backend, uv-managed), replacing `setup.py`
- Test suite and GitHub Actions CI: lint, type check, and tests on Python 3.11-3.14
- Pre-commit hooks (ruff format + lint, pyrefly, planners validation)
- PyPI publish workflow, disabled behind the `PUBLISH_ENABLED` repository variable
- Dependabot with grouped weekly updates

[Unreleased]: https://github.com/gitronald/wayback/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/gitronald/wayback/releases/tag/v0.1.0
