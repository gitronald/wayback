---
id: 4
slug: cdx-refusals-and-rate-limits
status: draft
branch:
created: 2026-11-06T23:02:39-08:00
concluded:
pr:
---

# Report CDX refusals and follow the archive's rate limits

## Plan

### Goal

Make a refused or throttled CDX lookup say so. Today the package can't tell
a refusal from a real answer, so a caller can't either. Also document what is
known about web.archive.org's rate limits and pace lookups to match. A
caller that looks up a few hundred article URLs and fetches their raw
copies found a refusal that surfaces as a timestamp parse error, and the
pacing evidence below. Neither is handled or documented in v0.3.0.

### Findings (v0.3.0, waybackpy 3.0.6)

1. **An HTML error page is parsed as CDX data.** For some single-URL
   queries, the CDX endpoint answers `403` with a short HTML page:

   ```
   <!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">
   <title>403 Forbidden</title>
   <h1>Forbidden</h1>
   <p>This type of CDX query requires authorization.</p>
   ```

   waybackpy's `get_response` returns the response whatever its status, and
   `cdx_api_manager` yields `res.text` without checking it. The first line
   splits on spaces into exactly 7 fields, the number a CDX line has, so it
   passes the field-count check in `snapshots()`. Parsing then fails with
   `ValueError: time data 'HTML' does not match format '%Y%m%d%H%M%S'`. The
   same URL gives the same result on a retry, so this is not throttling. The
   response object is not kept (only `last_api_request_url`), so the package
   cannot read the status afterwards.
2. **The caller sees a bug, not a refusal.** The `request` event reports
   `error_type: ValueError`. `_is_retryable` correctly does not retry it, but
   nothing says the server refused, or with what status. A 429 takes the
   same path (README, *Rate limits and retries*): it fails the same way or
   comes back empty.
3. **No request timeout.** Item 10 of plan 002's list still stands: waybackpy
   passes no timeout, so a stalled connection blocks a lookup indefinitely.
   The README documents it; nothing fixes it.
4. **The archive publishes no CDX or playback rate limit.** The
   best-sourced figures are secondhand, from EDGI's `wayback` library, whose
   maintainers say they set them with Internet Archive staff:
   - CDX search and timemap: one shared pool, **30 requests a minute**
     (EDGI `wayback` v0.5.1, issues #205 and #206, PR #208). EDGI's
     client defaults to 80% of that (24 a minute).
   - Playback (`/web/<timestamp>[id_]/<url>`): **600 a minute**
     (`DEFAULT_MEMENTO_RATE_LIMIT` in EDGI's `_client.py`).
   - A 429 with no `Retry-After` waits 60 s there. Since v0.5.0, EDGI
     raises rate-limit errors instead of retrying them and tells callers
     to pause every client, because retrying "can help" get an address
     blocked.
   - Unsourced but uncontradicted (`bellingcat/wayback-google-analytics`
     issue #21, 2023): 429s ignored for more than a minute lead to an
     hour's block of the address, doubling on each repeat.
   - Save Page Now's 15 a minute (`archive.org/details/toomanyrequests_20191110`)
     is the only limit the archive states itself, and does not apply here.
5. **Refusals come well under those figures.** The throttling seen is
   refused connections (errno 111), not 429s:
   - With lookups and raw-copy fetches sharing one 5 s gap (about 6 lookups
     a minute), roughly a fifth of the first hundred URLs saw a refused
     connection, spread through the run.
   - At a 10 s gap, a later run of about 215 lookups and 150 fetches saw
     none, and the package logged no `retry` events.

   `MIN_INTERVAL` (5 s) paces only the package's own lookups. A caller
   that fetches raw copies, as the README's example does, sends at least
   twice that rate to the same host, and the package doesn't count those
   fetches.
6. **A listed 200 capture can play back as 404.** One URL's only CDX record
   has `statuscode` 200, but its `id_` copy answers 404, repeatably.
   Nothing in the lookup is wrong. A caller should expect it.
7. **`save_log` is deprecated** since v0.3.0 and is still accepted.

### Decisions (proposed; see Questions)

1. **Typed errors.** A `CDXResponseError` carries `status`,
   `content_type`, the request URL, and the first few hundred characters of
   the body. It has two subclasses: `CDXRefused` for 401 and 403, and
   `RateLimited` for 429, which adds `retry_after`. Any non-2xx response, or
   a 2xx body that is not CDX text (its first non-blank line starts with
   `<`), raises one of them, never a parse error.
2. **Where the check lives** depends on question 1. The recommended way is
   to make the CDX request in the package with `requests`: one GET per page
   with the resume key, a `timeout`, and a status check before parsing.
   waybackpy stays for `CDXSnapshot` parsing, or is dropped from the lookup
   path. That also fixes finding 3. The fallbacks are wrapping waybackpy's
   `get_response`, which means patching a module attribute (fragile), or a
   body-only check, which cannot see the status.
3. **Events.** A `request` event gains `http_status` when there was a
   response, and two more outcomes: `refused` (401 or 403) and
   `rate_limited` (429). `error_type` names the new classes.
4. **Retry policy.**
   - `CDXRefused` is not retried.
   - `RateLimited` holds the process-wide pacer for `Retry-After`, or 60 s
     when the header is absent, so every lookup waits, not only the one
     that got the 429. It then retries within `max_retries` or raises
     (question 2).
   - A streak of refused connections across lookups (default 3 in a row,
     process-wide) raises `ArchiveUnreachable` instead of backing off
     again, since a firewall block may outlast a 5-10-20 s backoff and
     retries may extend it.
5. **One pace for every request to the host.** Expose the pacer, for
   example `wayback.wait_turn()`, so a caller's raw-copy fetches share
   `min_interval` with the lookups. The README's fetch example calls it
   instead of `time.sleep(5)`. Whether the default gap rises from 5 s is
   question 3.
6. **README.** Rewrite *Rate limits and retries* with:
   - the sourced figures from finding 4, cited to EDGI's library and saying
     the archive publishes none;
   - what refusals look like (finding 5);
   - the new errors and outcomes;
   - the playback 404 (finding 6);
   - for heavy users, a pointer to the Internet Archive's contact address,
     as its Save Page Now notice gives, rather than ways around the limits.
7. **`save_log`.** Removal is question 4.

### Tests

All tests use stubbed responses and make no network calls:

- 403 HTML, 401, 429 with and without `Retry-After`, and 503 then 200
- a 2xx HTML body
- a CDX body with a resume key
- an empty body
- a stalled request hitting the timeout
- the refused-connection streak

Also check:

- the event fields for each outcome
- that `wait_turn()` and lookups share one gap
- that a `RateLimited` pause holds a second lookup started meanwhile

### Order

1. Questions 1 to 4 answered.
2. Typed errors and the request path (decisions 1 and 2), with tests.
3. Events and retry policy (decisions 3 and 4).
4. Pacer helper and default gap (decision 5).
5. README and CHANGELOG (decision 6), and `save_log` per question 4.
6. A minor release, since the error types change what callers catch.

### Risks

- **Dropping waybackpy's request path** means owning resume-key paging,
  which waybackpy does in `cdx_api_manager`. The tests must cover a paged
  answer.
- **Callers that catch `WaybackError` or `ValueError`** today would see new
  types. Making `CDXResponseError` subclass `WaybackError` keeps their
  `except` working.
- **The rate-limit figures are secondhand** and can change without notice.
  The README says so and dates them.

### Out of scope

- Fetching pages for the caller (a fetch is the caller's policy; the README
  already says so). The pacer helper is the only change on that side.
- Paged CDX queries (`page`, `pageSize`). If they are ever added, keep
  `pageSize` at 9 or below (EDGI issue #205).
- Save Page Now.

## Handoff

**State (2026-11-06):** draft, nothing implemented.

**Questions:**

1. Make the CDX request in the package (`requests`, timeout, status
   check), wrap waybackpy's `get_response`, or only check the body?
   Recommended: the package makes the request.
2. On a 429, after the pause: retry within `max_retries`, or raise
   `RateLimited` to the caller, as EDGI's library does? Recommended: raise,
   after holding the pacer, since a caller in a batch is better placed to
   stop the batch.
3. Raise `MIN_INTERVAL` from 5 s to 10 s? 5 s is inside the 30-a-minute
   figure for lookups alone. Refusals came at 5 s once fetches shared the
   host, and stopped at 10 s.
4. Remove `save_log` in this release (a breaking change) or leave it
   deprecated for one more?
