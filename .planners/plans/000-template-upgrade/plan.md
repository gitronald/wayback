---
id: 0
slug: template-upgrade
status: active
branch: feature/template-upgrade
created: 2026-07-24T08:00:07-07:00
concluded:
pr:
---

# Upgrade repo to the proj-template standard

## Plan

Bring this repo up to the current proj-template standard (uv, ruff, pyrefly,
pre-commit, Claude hooks, GitHub Actions, planners, stanza) via the
install-template skill, Mode 2 (upgrade an existing repo).

### Classification

**package** — setuptools-based `setup.py`, an importable `wayback/` package
dir, installed via pip. No `pyproject.toml`, no tests, no CI, no `.planners/`
prior to this plan.

### Sync decisions (per matrix)

- `pyproject.toml` — create from the template (none exists), migrating
  metadata from `setup.py`: name, description, authors, and runtime deps
  (`waybackpy`, `requests`, `pandas`, `tqdm`). Hatchling build backend, sdist
  `only-include`, `[project.urls]`, ruff + pyrefly (strict) + pytest config,
  dev group. Version set to `0.1.0a0` (dev-branch alpha; `0.0.1` was never
  tagged or released). No `[project.scripts]` — the package has no CLI.
  `setup.py` is removed once `pyproject.toml` replaces it.
- `.pre-commit-config.yaml` — copy from template (ruff format/lint, pyrefly,
  planners-validate). No existing local hooks to preserve.
- `.python-version` — create at 3.14.
- `.gitignore` — merge template entries; keep repo-specific `data` and
  `notebooks` entries.
- `.claude/settings.json`, `.claude/hooks/lint-typecheck.sh`, `.claude/CLAUDE.md`
  — copy from template (none exist; CLAUDE.md adapted to this package).
  On-disk only: `.claude/` is gitignored per the template default.
- `.github/workflows/test.yml` — sync: full Python 3.11–3.14 matrix with the
  job-level `UV_PYTHON` pin.
- `.github/workflows/publish.yml` — sync (disabled by default behind the
  `PUBLISH_ENABLED` repo variable).
- `.github/dependabot.yml` — create with grouped weekly updates + cooldown for
  `github-actions` and `uv` ecosystems. Repo toggles: Dependabot alerts on,
  automated security fixes off.
- `.planners/` — scaffolded by this plan.
- `tests/` — none existed; add a minimal smoke test (import + public API
  presence, no network) so the pytest dev-group deps and the CI pytest step
  have something real to run. The package's own code is otherwise untouched
  except for lint/type fixes required by the verification gate.

### Deliberately skipped

- `README.md`, `CHANGELOG.md`, `LICENSE`, package source content — never
  synced per the matrix (lint/type fixes at the source excepted).
- Renovate variant — repo uses the Dependabot default.

## Log
