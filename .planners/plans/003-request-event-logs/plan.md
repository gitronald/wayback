---
id: 3
slug: request-event-logs
status: implemented
branch: feature/003-request-event-logs
created: 2026-11-06T21:22:25-08:00
concluded:
pr: https://github.com/gitronald/wayback/pull/17
---

# Log every lookup and request as structured events

## Plan

### Goal

Make every lookup observable after the fact. A caller tuning its pacing
against web.archive.org needs to know, for each request: when it went,
how long the package waited before it, how long it took, how it ended,
and what was retried. v0.2.0 records almost none of that, and records
nothing at all for `get_url_records`, the lookup that needs no DataFrame.
A downstream script that looks up a few hundred article URLs had to build
its own request log around the package to get it, and could still not see
the package's internal pacing or waybackpy's retries.

### Findings (v0.2.0, waybackpy 3.0.6)

1. **`get_url_records` logs nothing.** `save_log` and `log_path` exist
   only on `get_url_snapshots` and `get_url_list_snapshots`, so the
   records-first path (plan 002) has no run log at all.
2. **The run log is one record per call,** written by `_append_run_log`:
   parameters, duration, outcome, and snapshot count. A call that took
   three attempts and 40 s of backoff looks like one slow success.
3. **Retries are unstructured text.** `get_url_records` logs
   `retry %d/%d for %s in %.1fs: %s` as a warning on the `wayback`
   logger. A caller who wants them as data has to attach a handler and
   parse the positional args.
4. **Pacing waits are invisible.** `_Pacer.wait` sleeps and records only
   its own clock; how long a lookup waited for its turn is not reported.
5. **waybackpy's own retries are invisible.** It retries connection
   errors and 5xx responses 5 times inside one `snapshots()` call before
   the package sees anything.
6. **A throttled lookup can look like data.** waybackpy hands a 429's body
   back as if it were CDX output (README, "Rate limits and retries"), so
   the log cannot tell it from a real result or a parse error.

### Decisions (draft)

1. **One event stream, two shapes.** Each lookup emits:
   - `request`: one per attempt the package makes. Fields: `ts`, `url`,
     `attempt`, `waited_s` (the pacer's sleep before it), `duration_ms`,
     `outcome` (`ok`, `empty`, `error`), `n_records`, `error` (type and
     message), and the CDX parameters (`start_timestamp`, `end_timestamp`,
     `filters`, `collapses`).
   - `retry`: `ts`, `url`, `attempt`, `max_retries`, `delay_s`,
     `retry_after` (when the server sent one), `error`.
   - `lookup`: one per call, the summary the run log keeps today (its
     fields stay, as a superset), plus `attempts` and `total_waited_s`.
2. **Delivery by callback, with a file writer built on it.** Every lookup
   function takes `on_event: Callable[[dict], None] | None`. A
   `JsonlEventLog(path)` helper appends events as JSON lines.
   `log_path=` on any lookup function, `get_url_records` included, is
   shorthand for `on_event=JsonlEventLog(log_path)`.
3. **`save_log` stays for compatibility.** `save_log=True` with no
   `log_path` still writes `data/wayback-runs.jsonl`, now with the event
   shapes above. Whether to deprecate it is question 1.
4. **The retry warnings stay** on the `wayback` logger, for callers who
   use logging. They are emitted from the same place as the `retry` event,
   so the two cannot disagree.
5. **No HTTP-level detail the package cannot see.** waybackpy's internal
   retries and the 429-as-data case (findings 5 and 6) are noted in the
   README as gaps, and handled by the later rate-limit plan, not faked
   here.
6. **Tests** use a stub CDX client and a fake clock: events for a
   success, an empty result, a retried refusal, a final failure, and the
   pacer's wait, plus a `JsonlEventLog` round trip.
7. **Docs:** a README section, "Logging requests", with an example event
   per shape, and a CHANGELOG entry.

### Relation to other work

Research done alongside this plan on the Wayback Machine's rate limits
(none published by the Internet Archive for CDX or playback; 30
lookups/min and 600 playback fetches/min relayed by another client
library's maintainers; refused connections well under both) points to
changes in pacing, 429 detection, and block handling. Those belong in a
separate plan. This one supplies the data that plan will need.

### Out of scope

- Fetching archived pages (the package does not fetch).
- Changing pacing or retry defaults.
- Reading the log back to pace across processes. A caller can do it from
  the events; a helper can come later.

## Log

- **2026-11-06T21:31:04-08:00** — Activated.
  - Branch: `feature/003-request-event-logs`
  - Base: `dev` at `6ea78ff`
  - Worktree: `.worktrees/003-request-event-logs`
  - PR: pending
- **2026-11-06T21:34:43-08:00** — PR opened: https://github.com/gitronald/wayback/pull/17
- **2026-11-06T21:35:29-08:00** — Implemented the event log, with the three questions answered (deprecate `save_log`, no environment variable, caller-supplied `run_id`).
    - `get_url_records`, `get_url_snapshots`, and `get_url_list_snapshots` take `on_event`, `log_path`, and `run_id`. `log_path` alone turns the log on. `JsonlEventLog(path, run_id=None)` is exported.
    - Events: `request` per attempt, `retry` per retry (emitted beside the logger warning, from one place), `lookup` per call (every v0.2.0 run-log field, plus `attempts` and `total_waited_s`), and one addition to the draft: a `batch` event closing each `get_url_list_snapshots` run, which carries that function's old run record. Each URL in a batch now logs its own lookup events too.
    - `error` plus `error_type` (the exception class) on failed events, in place of the draft's single type-and-message field, so the lookup's `error` stays the string the old run log held.
    - `_Pacer.wait` returns the seconds it slept, reported as `waited_s`. `total_waited_s` is the pacer's waits plus the retry delays.
    - `save_log=True` raises a `DeprecationWarning` and still logs, to `log_path` or `LOG_PATH`.
    - Tests: 49 pass (11 new event tests, the four run-log tests rewritten for the event stream). ruff and pyrefly are clean (one pre-existing `tqdm` stub warning). Ran on the code committed as `e0b5fa5` (the docs commit after it touches no code), with `dev` not moved since activation, so no base merge was needed.
    - README section "Logging requests" with an example line per event shape and the gaps (waybackpy's internal retries, 429-as-data); CHANGELOG `[Unreleased]` entries under Added, Changed, and Deprecated.
- **2026-11-06T21:35:44-08:00** — Implemented: 4 commits ahead of `dev`.
- **2026-11-06T21:40:03-08:00** — Review gate run (medium, `/code-review`: correctness and simplification finders, one verifier) and its fixes committed as `harden event handlers and log writes after review`.
    - Review follow-up: 10 candidates, 5 kept. Actioned, each with a test: an empty `log_path` now counts as none (it raised on `open('')`); a handler that raises is not called again for the `lookup` event, and the lookup's own error stays as the exception's context; each handler gets a deep copy of the event, so editing it cannot change the next attempt's filters or the caller's `collapses`; one module-level lock serializes every `JsonlEventLog` write; the batch event takes its parameters from `_params`. Four of the five tests fail on the pre-fix code; the lock and batch tests are regression guards (the interleave was not reproduced, and the batch values already matched).
    - Conscious no-ops: per-event `makedirs` and open (negligible beside a paced request, and each event is on disk as written); the `_Params` TypedDict (pyrefly needs the types); `JsonlEventLog(run_id=...)` (for callers sharing one writer through `on_event`); the closure and context-manager structure.
    - Checks at `f31f71a`: ruff check, ruff format --check, pyrefly (the pre-existing `tqdm` stub warning only), pytest 55 passed. Review posted to the PR.

## Handoff

**State:** done. Implemented, reviewed, and merged through the PR; ships
in the next release (the CHANGELOG's `[Unreleased]` holds it).

**Not verified:** the events against the live CDX API. Every test uses the
fake client and clock; the first real run is the downstream caller's.

**Handed on:** removing `save_log` (question 1) to a later minor release,
not yet planned. Pacing, 429 detection, and block handling to the separate
rate-limit plan named under *Relation to other work*, not yet opened.

**Questions:**

1. Deprecate `save_log` in favor of `log_path` alone, or keep both?
   **Answered 2026-11-06: deprecate.** `log_path` (or `on_event`) alone
   turns the log on. `save_log=True` still works and raises a
   `DeprecationWarning`; a later minor release removes it.
2. Should a log be on by default once a caller names a path through an
   environment variable (for example `WAYBACK_LOG_PATH`), so a script gets
   one without code changes? Off by default with no variable either way.
   **Answered 2026-11-06: no environment variable.** The log is on only
   when the caller passes `log_path` or `on_event`, so the package never
   writes a file the caller did not name.
3. Should events carry a caller-supplied `run_id`, or should the package
   make one per process?
   **Answered 2026-11-06: caller-supplied.** Lookups and `JsonlEventLog`
   take an optional `run_id`, stamped on every event when given and left
   off when not. The package makes none.

## Retrospective

- The plan's three event shapes held. The one addition was a `batch` event,
  needed so `get_url_list_snapshots` keeps the record its old run log wrote.
- Keeping the `lookup` event a superset of the old run-log record made the
  format change cheap to explain: old readers filter on `event == "lookup"`.
- The review earned its keep on handler behavior, which the draft never
  specified: what a raising handler does, and whether a handler can change
  the lookup's state. Specify both up front the next time a callback is added.
- One Log entry named a commit that did not exist; it was caught and fixed
  before the push. Read a sha from `git log` before writing it down.
