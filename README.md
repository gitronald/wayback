# Wayback

## Overview

`wayback` is a simple Python package built on `waybackpy` and designed to further simplify the process of collecting historical snapshot data of URLs from the [Internet Archive's Wayback Machine](https://archive.org/). It's particularly useful if you want to save all of the IA's snapshot links (e.g. `https://web.archive.org/web/<timestamp>/https://<url_of_interest>`) for a particular set of URLs that you can then sort/filter/examine without having to search and click around the Wayback Machine's interface.

## Installation

```sh
pip install git+https://github.com/gitronald/wayback
```


## Example Usage

```python
import polars as pl

import wayback

pl.Config.set_tbl_width_chars(180)
pl.Config.set_fmt_str_lengths(80)

data = wayback.get_url_snapshots(
    url="lazerlab.net",
    start_timestamp="20180101",
    end_timestamp="20231231",
)
print(f"snapshots found: {len(data):,}")
show_cols = ["url", "statuscode", "archive_url", "timestamp"]
print(data.select(show_cols).head(5))
```

**Output**:
```
processing lazerlab.net
extracting items
reshaping items
snapshots found: 220
```

```
shape: (5, 4)
┌──────────────┬────────────┬────────────────────────────────────────────────────────────────────────┬────────────────┐
│ url          ┆ statuscode ┆ archive_url                                                            ┆ timestamp      │
│ ---          ┆ ---        ┆ ---                                                                    ┆ ---            │
│ str          ┆ str        ┆ str                                                                    ┆ str            │
╞══════════════╪════════════╪════════════════════════════════════════════════════════════════════════╪════════════════╡
│ lazerlab.net ┆ 200        ┆ https://web.archive.org/web/20180129175333/http://lazerlab.net:80/     ┆ 20180129175333 │
│ lazerlab.net ┆ 200        ┆ https://web.archive.org/web/20180203075259/http://www.lazerlab.net:80/ ┆ 20180203075259 │
│ lazerlab.net ┆ 200        ┆ https://web.archive.org/web/20180301214059/http://lazerlab.net:80/     ┆ 20180301214059 │
│ lazerlab.net ┆ 200        ┆ https://web.archive.org/web/20180306094604/http://www.lazerlab.net:80/ ┆ 20180306094604 │
│ lazerlab.net ┆ 200        ┆ https://web.archive.org/web/20180314153547/http://lazerlab.net/        ┆ 20180314153547 │
└──────────────┴────────────┴────────────────────────────────────────────────────────────────────────┴────────────────┘
```


### Batch Collection

`get_url_list_snapshots` runs the same collection over a list of URLs, saving each
result to `<output_directory>/<md5(url)>.csv`:

```python
import wayback

wayback.get_url_list_snapshots(
    urls=["lazerlab.net", "ronalderobertson.com"],
    start_timestamp="20180101",
    end_timestamp="20231231",
    output_directory="data/snapshots",
)
```

URLs whose output file already exists are skipped, so an interrupted run can be
restarted without re-collecting anything. Requests are spaced `sleep_time` seconds
apart (default 5) to be polite to the API, and an error on one URL is printed and
skipped rather than stopping the batch.

A URL with no snapshots gets a header-only CSV.


### Defaults

- **Date range**: `get_url_snapshots` and `get_url_list_snapshots` search from the
  first capture to today.
  `start_timestamp=None` (the default) sets no lower bound, and
  `end_timestamp=None` means the date of the call. Pass `YYYYMMDD` strings to narrow
  it.
- **No snapshots**: an empty result is a polars DataFrame with zero rows and the usual
  columns (`wayback.SNAPSHOT_COLUMNS`), so `len(data)` is the snapshot count.
- **User-Agent**: requests identify themselves as `wayback/<version>`. Pass
  `user_agent=` to send another.
- **Event log**: off by default. Nothing is written unless a lookup is given
  `log_path=` or `on_event=` (see [Logging requests](#logging-requests)).


### Filters and records

`filters` and `collapses` go to the CDX API unchanged (`[!]field:regex`, and a field
such as `digest`), and `status` is a shortcut for `statuscode:<status>`. All three are
accepted by `get_url_records`, `get_url_snapshots`, and `get_url_list_snapshots`.

`get_url_records` returns the snapshots as a list of dicts with string values (the
fields in `wayback.RECORD_COLUMNS`), or `[]` when there are none. It does not need
polars. `get_url_snapshots` builds its DataFrame from the same records.

```python
import wayback

records = wayback.get_url_records(
    "https://example.com/news/article",
    status=200,
    collapses=["digest"],  # one row per distinct capture
)
earliest = records[0] if records else None
```


### Fetching an archived copy

Each record carries `archive_url`, the page as the Wayback Machine shows it (with its
toolbar and rewritten links), and `raw_url`, the page as captured:
`https://web.archive.org/web/<timestamp>id_/<original>`. `wayback.raw_url(timestamp,
original)` builds the same URL.

The package does not fetch pages. A fetch is a policy each caller sets (redirects, size
limits, which hosts to allow), so do it yourself, with a timeout, and keep it on the
same pacing as the lookups:

```python
import time

import requests

for record in records[:1]:
    time.sleep(5)  # stay on the same pacing as the lookups
    response = requests.get(
        record["raw_url"],
        headers={"User-Agent": wayback.wayback.DEFAULT_UA},
        timeout=30,
    )
    response.raise_for_status()
    html = response.text
```


### Rate limits and retries

web.archive.org throttles clients mainly by refusing connections
(`ConnectionError`, errno 111), not with 429 responses. One run of lookups and
snapshot fetches at one request every 5 seconds had requests refused for 20 of its
first 94 URLs, spread through the run (2026-11-06). The package therefore:

- **Paces lookups.** It keeps `min_interval` seconds (default 5) between any two
  lookups it starts in a process, through any of its functions, single lookups
  included. `get_url_list_snapshots` also sleeps `sleep_time` seconds after each URL.
  The CDX API may page a large result, and pages within one lookup are not paced.
- **Retries refusals.** A lookup that fails with a connection error, or after
  waybackpy's own retries on a 500, 502, 503, or 504 run out, is retried up to
  `max_retries` times (default 3). Each retry waits for an exponential backoff with
  jitter (5 s, 10 s, 20 s, plus up to 5 s), or for the `Retry-After` an error response
  names, up to 300 s, and is logged as a warning on the `wayback` logger and as a
  `retry` event (see [Logging requests](#logging-requests)).

Two limits come from waybackpy 3.0.6, which makes the HTTP requests:

- **No timeout.** waybackpy sends its requests with no timeout and takes no argument
  for one, so a connection that stalls can block a lookup indefinitely. Run long
  batches where a stuck process can be noticed and restarted.
  `get_url_list_snapshots` skips URLs it has already saved, so a restart repeats no
  work.
- **429 responses are not errors.** waybackpy returns the body of a 429 as if it were
  CDX data, so a throttled lookup can raise a `WaybackError` about the number of
  snapshot properties, or come back empty. The package cannot tell either case from a
  real one, so it does not retry them. If lookups start failing this way, slow down
  (raise `min_interval`).


### Logging requests

Every lookup function (`get_url_records`, `get_url_snapshots`, and
`get_url_list_snapshots`) can report what it did as events, one dict each:

- `request`: one per attempt at a lookup, with how long the pacer waited before it
- `retry`: one per retry the package makes, with its delay and the error that caused it
- `lookup`: one per call, as it ends, with its outcome, attempts, and total wait
- `batch`: one per `get_url_list_snapshots` run, after its URLs' own events, with
  counts and per-URL outcomes

Pass `log_path=` to append them to a JSON Lines file, `on_event=` to receive them in
code, or both. `run_id=` stamps an id of the caller's choosing on every event, so the
lookups of one run can be told apart in a shared log:

```python
import wayback

records = wayback.get_url_records(
    "https://example.com/news/article",
    status=200,
    log_path="logs/wayback-requests.jsonl",
    run_id="2026-11-06T21-30",
)

# Or handle the events yourself, e.g. to add fields or write them elsewhere
events = []
wayback.get_url_records("https://example.com/", on_event=events.append)
```

`wayback.JsonlEventLog(path, run_id=None)` is the writer behind `log_path`, for
callers who want to share one between functions or add it to their own handler. An
exception raised by `on_event` is not caught.

Events record each URL looked up, so a log of private or sensitive URLs is itself
sensitive: keep it where the URLs would be kept.

A lookup refused once and then answered writes four lines:

```json
{"event": "request", "ts": "2026-11-06T21:30:00.120-08:00", "run_id": "2026-11-06T21-30", "url": "https://example.com/news/article", "attempt": 1, "waited_s": 4.2, "duration_ms": 31, "outcome": "error", "n_records": null, "error": "[Errno 111] Connection refused", "error_type": "ConnectionError", "start_timestamp": null, "end_timestamp": "20261106", "filters": ["statuscode:200"], "collapses": []}
{"event": "retry", "ts": "2026-11-06T21:30:00.151-08:00", "run_id": "2026-11-06T21-30", "url": "https://example.com/news/article", "attempt": 1, "max_retries": 3, "delay_s": 7.31, "retry_after": null, "error": "[Errno 111] Connection refused", "error_type": "ConnectionError"}
{"event": "request", "ts": "2026-11-06T21:30:08.902-08:00", "run_id": "2026-11-06T21-30", "url": "https://example.com/news/article", "attempt": 2, "waited_s": 0.0, "duration_ms": 1420, "outcome": "ok", "n_records": 12, "start_timestamp": null, "end_timestamp": "20261106", "filters": ["statuscode:200"], "collapses": []}
{"event": "lookup", "ts": "2026-11-06T21:30:08.903-08:00", "run_id": "2026-11-06T21-30", "function": "get_url_records", "url": "https://example.com/news/article", "user_agent": "wayback/0.3.0", "start_timestamp": null, "end_timestamp": "20261106", "filters": ["statuscode:200"], "collapses": [], "started_at": "2026-11-06T21:29:55.920-08:00", "duration_seconds": 12.983, "attempts": 2, "total_waited_s": 11.51, "outcome": "success", "n_snapshots": 12}
```

A request's `outcome` is `ok`, `empty` (the lookup was answered with no snapshots), or
`error`; a lookup's is `success` or `error`. `error` and `error_type` appear only on a
failed request, retry, or lookup. `total_waited_s` counts the pacer's waits and the
retry delays. The `lookup` event carries every field the earlier run log wrote, so a
reader of that log can keep reading `lookup` lines.

The events cover what the package can see. waybackpy's own retries inside one attempt,
and a 429 whose body waybackpy returns as data (see
[Rate limits and retries](#rate-limits-and-retries)), are not reported as separate
events: they show only as a slow or failed `request`.

`save_log=True` on `get_url_snapshots` and `get_url_list_snapshots` is deprecated. It
still logs the events, to `log_path` or to `data/wayback-runs.jsonl` relative to the
working directory when no path is given, and raises a `DeprecationWarning`. Pass
`log_path=` instead.


## Dependencies

`waybackpy`: For interacting with the Wayback Machine API.  
`requests`: For making HTTP requests.  
`hashlib`: For generating hash values from URLs.  
`polars`: For the DataFrame functions (`get_url_snapshots`, `get_url_list_snapshots`); `get_url_records` and `raw_url` import without it.  
`tqdm`: For providing progress bars during data collection.  


## Acknowledgments
This package is primarily a wrapper for [`waybackpy`](https://akamhy.github.io/waybackpy/), which provides a broader range of tools for using the [Wayback Machine CDX Server API](https://github.com/internetarchive/wayback/tree/master/wayback-cdx-server) provided by the [Internet Archive](https://archive.org/). 
