# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Maintenance: synced the project tooling with repotemplates 0.15.0 (release stamp and
  the Dependabot guide link). No change to the library's behavior or API

## [0.3.0] - 2026-11-06

### Added

- Request events: every lookup function takes `on_event=` (a callable given each
  event), `log_path=` (append the events to a JSON Lines file), and `run_id=` (an id
  stamped on every event). Events are `request` (one per attempt, with the pacer's
  wait, duration, outcome, and error), `retry` (delay, `Retry-After`, error), `lookup`
  (one per call, a superset of the old run log record, plus `attempts` and
  `total_waited_s`), and `batch` (one per `get_url_list_snapshots` run)
- `get_url_records` can now log, through the same arguments
- `JsonlEventLog(path, run_id=None)`, the JSON Lines writer behind `log_path`
- README section "Logging requests"

### Changed

- `log_path` alone turns the log on; it no longer needs `save_log=True`
- The run log is a stream of events, not one record per call: read the `lookup` lines
  (and `batch` for `get_url_list_snapshots`) for the records the old log held

### Deprecated

- `save_log`: pass `log_path=` (or `on_event=`) instead. `save_log=True` still logs, to
  `log_path` or `data/wayback-runs.jsonl`, and raises a `DeprecationWarning`

## [0.2.0] - 2026-11-06

### Added

- `log_path` argument on `get_url_snapshots` and `get_url_list_snapshots`, naming
  where `save_log=True` writes the run log
- `filters`, `collapses`, and `status` arguments on `get_url_snapshots` and
  `get_url_list_snapshots`, passed to the CDX API (`status=200` adds
  `statuscode:200`)
- `get_url_records`: the snapshots as a list of dicts with string values, `[]` when
  there are none, importable without polars. `get_url_snapshots` is built on it
- `raw_url(timestamp, original)`, the URL of an archived page's raw `id_` copy, and a
  `raw_url` field on every record and DataFrame row
- Pacing: at least `min_interval` seconds (default 5) between any two lookups in a
  process, single lookups included
- Retries: a lookup refused with a connection error, or failing after waybackpy's own
  retries on 5xx responses, is retried up to `max_retries` times (default 3) with
  exponential backoff and jitter, honoring `Retry-After` up to 300 seconds
- README sections on fetching archived copies and on the Wayback Machine's rate
  limits, including waybackpy's lack of a request timeout

### Changed

- The run log is off by default: `save_log` now defaults to `False`, so a call no
  longer creates `data/wayback-runs.jsonl` in the working directory. Pass
  `save_log=True` (and optionally `log_path`) to keep writing it
- `start_timestamp` defaults to `None`, so a lookup has no lower bound and includes
  1996-1999 captures, which the old `"20000101"` default dropped
- `end_timestamp` defaults to `None`, meaning today at the time of the call, not the
  date the module was imported
- `get_url_list_snapshots` takes the same timestamp defaults, so `start_timestamp`
  and `end_timestamp` are optional there too
- An empty result is a DataFrame with zero rows and the usual columns, not a single
  row of NaNs, and `get_url_list_snapshots` writes a header-only CSV for it
- The default User-Agent is `wayback/<version>`, replacing a Firefox 40 browser
  string
- polars replaces pandas: `get_url_snapshots` returns a `polars.DataFrame`, and
  pandas is no longer a dependency. Code that used pandas methods on the result
  (`.iloc`, `.to_dict`, `.to_csv`) needs the polars equivalents, or
  `.to_pandas()` with pandas and pyarrow installed
- polars is imported only by the DataFrame functions, not when the package is
  imported
- `datetime_timestamp` is parsed from `timestamp`, so the column is a datetime
  column whether or not the result is empty
- `get_url_list_snapshots` writes its CSVs with polars, so `datetime_timestamp` and
  `retrieved_on` are ISO 8601 (`2020-01-01T00:00:00.000000`), not pandas'
  `2020-01-01 00:00:00`

## [0.1.2] - 2026-11-06

### Changed

- The sdist now ships `CHANGELOG.md`
- Locked dependencies updated, including the security fixes in urllib3 2.8.0 and
  certifi 2026.10.19, and pandas 3.1.0 and tqdm 4.70.1
- Development tooling and CI synced with the project template: a coverage gate on the
  test run, ruff 0.17 and pyrefly 1.4, and SHA-pinned GitHub Actions

## [0.1.1] - 2026-07-24

### Added

- `save_log` argument (default `True`) on `get_url_snapshots` and
  `get_url_list_snapshots`, appending a JSON record of each run — parameters,
  timing, and outcome — to `data/wayback-runs.jsonl`

## [0.1.0] - 2026-07-24

### Added

- `get_url_snapshots`: collect Wayback Machine CDX snapshots for a URL into a DataFrame
- `get_url_list_snapshots`: batch-collect snapshots for a list of URLs into per-URL CSV files
- Packaging via `pyproject.toml` (hatchling build backend, uv-managed), replacing `setup.py`
- Test suite and GitHub Actions CI: lint, type check, and tests on Python 3.11-3.14
- Pre-commit hooks (ruff format + lint, pyrefly, planners validation)
- PyPI publish workflow, disabled behind the `PUBLISH_ENABLED` repository variable
- Dependabot with grouped weekly updates

[Unreleased]: https://github.com/gitronald/wayback/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/gitronald/wayback/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/gitronald/wayback/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/gitronald/wayback/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/gitronald/wayback/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/gitronald/wayback/releases/tag/v0.1.0
