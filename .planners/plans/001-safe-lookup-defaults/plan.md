---
id: 1
slug: safe-lookup-defaults
status: done
branch: feature/001-safe-lookup-defaults
created: 2026-11-06T20:34:24-08:00
concluded: 2026-11-06T20:49:01-08:00
pr: https://github.com/gitronald/wayback/pull/14
---

# Make lookup defaults safe and empty results empty

## Plan

### Goal

Fix five defaults and behaviors in `wayback/wayback.py` (v0.1.2) that
surprised a downstream script using `get_url_snapshots` to look up archived
copies of a few hundred article URLs. Each one either writes where the
caller did not ask, misrepresents the client, or hands back data the caller
has to clean. None needs a new feature; plan 002 holds those.

### Findings (v0.1.2)

1. **The run log writes into the caller's working directory by default.**
   `save_log=True` on both functions, and `LOG_PATH =
   "data/wayback-runs.jsonl"` is relative, so any project that calls the
   library gains a `data/wayback-runs.jsonl`. The downstream script had to
   pass `save_log=False` to keep it out of its repository.
2. **The default User-Agent poses as an old browser.** `DEFAULT_UA` is a
   Firefox 40 on Windows XP string. A library should say what it is.
3. **`end_timestamp` defaults to the date the module was imported.** The
   default `time.strftime("%Y%m%d", time.localtime())` is evaluated once,
   when the function is defined, so a process that runs past midnight
   queries up to a stale date.
4. **`start_timestamp` defaults to `"20000101"`,** which drops 1996 to 1999
   captures without saying so. The CDX API has no lower bound by default.
5. **An empty result is a one-row frame of NaNs.** `pd.DataFrame({},
   index=[0])` gives a row with no `timestamp`, with `url`, `retrieved_on`,
   and `retrieved_dates` filled in, so a caller must filter it out, and
   `_count_snapshots` exists to do so. `get_url_list_snapshots` writes that
   row to the URL's CSV.

### Decisions

1. **`save_log` defaults to `False`** on both functions, and a new
   `log_path: str | None = None` argument says where to write. `save_log=True`
   with no `log_path` keeps writing `LOG_PATH`, so a caller that opts in gets
   today's behavior. A behavior change: callers that relied on the log being
   written must now pass `save_log=True`.
2. **`DEFAULT_UA` becomes `wayback/<version>`** (from
   `importlib.metadata.version("wayback")`, falling back to `wayback` when
   the package metadata is absent). `user_agent` stays for callers that need
   another. Do not add a contact address to the default.
3. **`end_timestamp: str | None = None`**, resolved to today inside the
   call. The `get_url_list_snapshots` signature already requires both
   timestamps and does not change.
4. **`start_timestamp: str | None = None`**, passed through as `None` so the
   CDX query has no lower bound. waybackpy's `WaybackMachineCDXServerAPI`
   already takes `None` for both.
5. **An empty result is an empty frame with the usual columns**: the
   CDX snapshot fields (`urlkey`, `timestamp`, `datetime_timestamp`,
   `original`, `mimetype`, `statuscode`, `digest`, `length`, `archive_url`)
   plus `url`, `retrieved_on`, and `retrieved_dates`, in the same order as a
   non-empty result. `_count_snapshots` becomes `len(df)`; keep the helper
   if the log still uses it. An empty URL's CSV is header-only.
6. **Release as 0.2.0.** Decisions 1, 4, and 5 change what existing calls
   return or write; pre-1.0, that is a minor bump. The changelog's
   `Changed` section names each one.

### Tests

- No log file appears in a temporary working directory with the defaults;
  one appears at `log_path` with `save_log=True`, and at `LOG_PATH` with no
  path.
- The default User-Agent starts with `wayback/` and reaches the CDX
  constructor (mock `WaybackMachineCDXServerAPI`).
- `end_timestamp` resolves at call time (freeze or patch the clock between
  two calls); `start_timestamp=None` reaches the constructor as `None`.
- An empty CDX answer gives zero rows and the full column list; the list
  function writes a header-only CSV for it.
- No test touches the network.

### Order

1, 2, 3, and 4 are independent one-line changes with a test each; 5 next,
since it touches the list function's CSV; then the README's usage examples
and the changelog. One PR.

### Out of scope

- New lookup features (filters, a records API, archived-copy fetching,
  retries): plan 002.
- Dropping pandas: plan 002 decides it.

## Log

- **2026-11-06T20:39:16-08:00** — Activated.
  - Branch: `feature/001-safe-lookup-defaults`
  - Base: `dev` at `2ceb7c3`
  - Worktree: `.worktrees/001-safe-lookup-defaults`
  - PR: pending
- **2026-11-06T20:40:52-08:00** — PR opened: https://github.com/gitronald/wayback/pull/14
- **2026-11-06T20:41:23-08:00** — Implemented decisions 1-5 in two commits on the branch.
  - `save_log` defaults to `False` on both functions; new `log_path` argument, falling back to `LOG_PATH`.
  - `DEFAULT_UA` is `wayback/<version>` from package metadata, `wayback` without it.
  - `start_timestamp`/`end_timestamp` default to `None`; the end resolves to today inside the call (via `_today()`), the start passes through as no lower bound. `retrieved_dates` writes an empty start as `-<end>`.
  - Empty results are zero-row frames with `SNAPSHOT_COLUMNS` (now exported); `_count_snapshots` is gone, replaced by `len(df)`. An empty URL's CSV is header-only.
  - Tests fake `WaybackMachineCDXServerAPI`; 14 pass, coverage 93%. ruff and pyrefly clean at the docs commit.
  - README gains a Defaults section; the changelog's `[Unreleased]` names each change under Added/Changed.
  - Not done here: the 0.2.0 release (decision 6). The release is left for close-out, since plan 002 stacks on this branch and may share the release.
- **2026-11-06T20:41:25-08:00** — Implemented: 4 commits ahead of `dev`.
- **2026-11-06T20:49:17-08:00** — Review gate run on PR 14 (code-review, medium); review posted as a PR comment.
  - Review follow-up: correctness finder raised nothing. Two test-only findings fixed in 3b3bef7: the error-log test now uses the fake CDX fixture's refuse set instead of its own stub, and the start-timestamp test no longer repeats the empty-frame column check. Rejected as pre-existing: the localtime() argument in _today(). No new regression tests, since both fixes are to tests; 14 pass, ruff, format, and pyrefly clean.
  - Decision 6 (release) moves to the release pipeline run after plan 002 closes, so 001 and 002 ship in one minor release.

## Retrospective

- All five findings were fixed as the decisions specified; none needed a second pass. Decision 6 (the release) is the one piece that moved, so it can ship with plan 002 in one minor version.
- Building the empty frame from `pd.DataFrame(items, columns=CDX_COLUMNS)` removed both the NaN placeholder row and `_count_snapshots`. Fixing the frame's shape at the source made the downstream cleanup helper unnecessary.
- A fake CDX client with `calls`, `rows`, and `refuse` covered every test without network access. Review found the one test that bypassed it, which is a reason to route every new test through the fixture.
- Stacking 002 on this branch with a `--no-ff` merge kept both plans moving. The cost is that 002 has to take in this branch's review fixes before it closes.
