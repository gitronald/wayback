---
id: 2
slug: lookup-filters-records-and-retries
status: active
branch: feature/002-lookup-filters-records-and-retries
created: 2026-11-06T20:34:25-08:00
concluded:
pr: https://github.com/gitronald/wayback/pull/15
---

# Add status filters, record output, raw copies, and retries

## Plan

### Goal

Give callers what a downstream script had to build for itself when it
used v0.1.2 to find and fetch archived copies of a few hundred article
URLs: CDX filters, results as plain records, the URL of an archived page's
raw copy, and lookups that survive web.archive.org's throttling. Plan 001
fixes the defaults first; this plan builds on its signatures.

### Findings (v0.1.2, waybackpy 3.0.6)

1. **No status or collapse filter.** waybackpy's
   `WaybackMachineCDXServerAPI` takes `filters` (`[!]field:regex`, e.g.
   `statuscode:200`) and `collapses` (e.g. `digest`), and validates both, but
   `get_url_snapshots` passes neither. A popular page returns every capture:
   317 rows for one news article, where the caller wanted the earliest 200.
2. **pandas for a list of records.** A lookup returns a DataFrame. The
   downstream script turned it straight back into a list of dicts
   (`json.loads(df.to_json(orient="records"))`), and pulled in pandas, a
   large dependency, to do it.
3. **No helper for the archived copy itself.** The package lists snapshots
   and stops. Every caller builds
   `https://web.archive.org/web/<timestamp>id_/<original>` (the raw `id_`
   form, without the Wayback toolbar or link rewriting) and fetches it on
   its own. The CDX row's own `archive_url` is the rewritten form.
4. **No pacing between single lookups, and not every refusal is retried.**
   Only `get_url_list_snapshots` sleeps (5 s, between URLs). waybackpy's
   `get_response` retries 5 times with a 0.5 backoff factor on connection
   errors and 500/502/503/504, but not on 429, and passes no timeout, so a
   stalled connection can block indefinitely. web.archive.org throttles by
   refusing connections (`ConnectionError`, errno 111): with one request
   every 5 s, lookups and snapshot fetches together, it refused requests for
   20 of the first 94 URLs, spread through the run, and 4 of those were
   lookups that had already used up waybackpy's retries (2026-11-06).

### Decisions

1. **Filters pass through.** `get_url_snapshots` (and the records function
   below) take `filters: list[str] | None` and `collapses: list[str] | None`,
   handed to waybackpy unchanged, plus a `status: int | None` shortcut that
   adds `statuscode:<status>`. `get_url_list_snapshots` takes the same.
2. **A records function underneath.** A new `get_url_records(...) ->
   list[dict[str, str]]` returns the CDX rows as dicts (the CDX fields, with
   `timestamp` as given, no datetime objects), and `[]` when there are none.
   `get_url_snapshots` becomes a DataFrame built on it, so the two cannot
   disagree.
3. **pandas becomes an optional extra in a later major, not now.** This plan
   moves the pandas import into the DataFrame functions so the records path
   imports without it, and records the extra (`wayback[pandas]`) as a
   follow-up for 1.0: making it optional now would break every
   `pip install wayback` caller of `get_url_snapshots`.
4. **`raw_url(timestamp, original) -> str`** returns the `id_` form, and
   `get_url_records` adds it to each row as `raw_url`. Fetching stays the
   caller's: an HTTP fetch with redirects, size caps, and address checks is a
   policy each caller sets, and the package has no fetch today. Document the
   pattern in the README instead.
5. **Pacing and retries in the package.**
   - A module-level pacer keeps at least `min_interval` seconds (default 5)
     between any two CDX requests the package makes in a process, single
     lookups included; `get_url_list_snapshots`' `sleep_time` keeps its
     meaning on top.
   - Retry a lookup on `ConnectionError`, 429, and 503 with exponential
     backoff and jitter (`max_retries`, default 3, after waybackpy's own),
     honoring `Retry-After` when present.
   - A per-request timeout, if waybackpy can take one; if it cannot,
     record that limit in the README and in Risks rather than patching
     waybackpy.
   - A README section on the limits observed (refused connections rather
     than 429s, the rate that drew them) and on keeping archived-page
     fetches on the same pacing.
6. **Release as 0.3.0** (after plan 001's 0.2.0): new arguments and
   functions, no removals.

### Tests

- Filters, collapses, and `status` reach the CDX constructor (mocked).
- `get_url_records` returns `[]` for an empty answer, dicts for a non-empty
  one, and `get_url_snapshots` matches it row for row.
- The records path imports with pandas absent (a subprocess with pandas
  blocked from import).
- `raw_url` builds the `id_` form; the rows carry it.
- The pacer sleeps the gap between two calls (fake clock); a refused
  connection, then a success, retries once; three refusals raise after
  `max_retries`; `Retry-After` is honored.
- No test touches the network.

### Order

1 and 2 together (the records function is where filters land), then 4,
then 5, then 3's import move and the README. One PR; split 5 into its own
if the waybackpy timeout question needs an upstream change.

### Risks

- **waybackpy has no timeout argument** that this package can reach;
  patching its session is fragile across versions. Check its current
  release first.
- **Retries compound.** waybackpy already retries 5 times; this plan's
  retries wrap those. Keep the outer count small and log each retry.

### Out of scope

- Fetching archived pages inside the package (decision 4).
- Save Page Now (asking the Wayback Machine to capture a URL).
- Dropping pandas as a dependency before 1.0 (decision 3).

## Log

- **2026-11-06T20:41:33-08:00** — Activated.
  - Branch: `feature/002-lookup-filters-records-and-retries`
  - Base: `dev` at `4a9bb08`
  - Worktree: `.worktrees/002-lookup-filters-records-and-retries`
  - PR: pending
- **2026-11-06T20:44:08-08:00** — PR opened: https://github.com/gitronald/wayback/pull/15
- **2026-11-06T20:44:50-08:00** — Implemented decisions 1, 2, 4, and 5, and decision 3's import move, stacked on plan 001's branch (merged in with `--no-ff`, since this plan builds on its signatures). PR 15 shows 001's commits until PR 14 merges.
  - `get_url_records` returns `list[dict[str, str]]` with the fields in `RECORD_COLUMNS` (CDX fields, `archive_url`, `raw_url`). `get_url_snapshots` builds its frame from it and parses `datetime_timestamp` with pandas; `SNAPSHOT_COLUMNS` gains `raw_url` after `archive_url`.
  - `filters`, `collapses`, and `status` on all three lookup functions; `status` appends `statuscode:<status>` to a copy of `filters`. The run log records the resolved filters.
  - `raw_url(timestamp, original)` builds the `id_` form. Fetching stays the caller's; the README shows the pattern.
  - Pacer: a module-level `_Pacer` (lock, injectable clock and sleep) keeps `min_interval` (default 5 s) between lookup attempts in a process. Pages inside one waybackpy lookup are not paced, since waybackpy offers no hook between them.
  - Retries: up to `max_retries` (default 3) on `requests.ConnectionError` or the builtin one, on `requests.exceptions.RetryError` (waybackpy's urllib3 retries on 500/502/503/504 ran out), and on an error whose response is 429 or 503. Backoff 5 s * 2^n plus up to 5 s jitter, or `Retry-After` (seconds or HTTP date). Each retry is a warning on the `wayback` logger.
  - Risk confirmed: waybackpy 3.0.6, its newest release on PyPI, calls `session.get` with no timeout and takes no timeout argument. Not patched; recorded in the README.
  - Found while checking 429s: waybackpy does not raise on a 429. It parses the body as CDX text, so a 429 surfaces as a `WaybackError` about the property count, or as an empty result. The package cannot recognize either, so neither is retried; the README says so. Only an error that carries a response (none does from waybackpy today) gets the 429 retry.
  - Decision 3 follow-up, not started: the `wayback[pandas]` optional extra for 1.0.
  - Not done here: the 0.3.0 release (decision 6). With 001 unreleased too, both may ship as one minor release at close-out.
  - Tests: 32 pass, coverage 96%, all with the CDX client faked and the pacer on a fake clock; a subprocess with pandas blocked imports the package and runs `get_url_records`. ruff and pyrefly clean.
