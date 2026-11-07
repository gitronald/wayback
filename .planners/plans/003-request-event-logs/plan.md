---
id: 3
slug: request-event-logs
status: draft
branch:
created: 2026-11-06T21:22:25-08:00
concluded:
pr:
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

## Handoff

**State:** draft, not started.

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
