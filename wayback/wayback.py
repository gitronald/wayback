from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import os
import random
import threading
import time
import warnings
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import datetime
from email.utils import parsedate_to_datetime
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, TypedDict

import requests
import tqdm
from waybackpy import WaybackMachineCDXServerAPI

if TYPE_CHECKING:
    import polars as pl

logger = logging.getLogger("wayback")


def _default_user_agent() -> str:
    """Name the package and its version, e.g. `wayback/0.2.0`."""
    try:
        return f"wayback/{version('wayback')}"
    except PackageNotFoundError:
        return "wayback"


DEFAULT_UA = _default_user_agent()
# Where the deprecated `save_log=True` writes when no `log_path` is given
LOG_PATH = "data/wayback-runs.jsonl"

# Seconds kept between any two lookups the package starts in a process
MIN_INTERVAL = 5.0
# Retries of a refused or throttled lookup, on top of waybackpy's own
MAX_RETRIES = 3
# Base of the exponential backoff between retries, in seconds
BACKOFF_SECONDS = 5.0
# Longest wait a server's `Retry-After` can impose on one retry, in seconds
MAX_RETRY_AFTER = 300.0
# HTTP statuses retried when an error carries its response
RETRY_STATUSES = frozenset({429, 503})

# Fields of a record from `get_url_records`, in order
RECORD_COLUMNS = [
    "urlkey",
    "timestamp",
    "original",
    "mimetype",
    "statuscode",
    "digest",
    "length",
    "archive_url",
    "raw_url",
]
# Columns of a snapshot frame from `get_url_snapshots`, in order; an empty
# result carries them too
SNAPSHOT_COLUMNS = [
    "url",
    "urlkey",
    "timestamp",
    "datetime_timestamp",
    *RECORD_COLUMNS[2:],
    "retrieved_on",
    "retrieved_dates",
]

# An event: a JSON-serializable dict whose `event` key names its shape
Event = dict[str, object]
EventHandler = Callable[[Event], None]


class _Pacer:
    """Keep a minimum gap between lookups, across every caller in a process."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.clock = clock
        self.sleep = sleep
        self._last: float | None = None
        self._lock = threading.Lock()

    def wait(self, min_interval: float) -> float:
        """
        Sleep until `min_interval` seconds have passed since the last lookup.

        Returns:
            float: Seconds slept, 0 when the gap had already passed.
        """
        with self._lock:
            now = self.clock()
            slept = 0.0
            if self._last is not None:
                gap = self._last + min_interval - now
                if gap > 0:
                    self.sleep(gap)
                    slept = gap
                    now = self.clock()
            self._last = now
            return slept


_pacer = _Pacer()

# Serializes every JsonlEventLog write in the process, so lookups that each
# build their own writer for one path cannot interleave their lines
_write_lock = threading.Lock()


class JsonlEventLog:
    """
    An event handler that appends each event to a JSON Lines file, one object
    per line, creating the file's directory when it is missing. Writes from
    every instance in a process take one lock, so threads can log to the same
    file through one writer or several.

    Args:
        path (str | os.PathLike[str]): File to append to.
        run_id (str | None): Stamped as `run_id` on every event that does not
            already carry one.
    """

    def __init__(self, path: str | os.PathLike[str], run_id: str | None = None):
        self.path = os.fspath(path)
        self.run_id = run_id

    def __call__(self, event: Event) -> None:
        if self.run_id is not None and "run_id" not in event:
            event = {**event, "run_id": self.run_id}
        line = json.dumps(event, default=str) + "\n"
        with _write_lock:
            log_dir = os.path.dirname(self.path)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)


class _Emitter:
    """
    Build events and hand each to every handler; does nothing with none. Each
    handler gets its own deep copy, so a handler that edits an event changes
    nothing the lookup goes on to use. An exception a handler raises is kept
    as `handler_error` and raised on, so the lookup can tell it from its own.
    """

    def __init__(self, handlers: list[EventHandler], run_id: str | None) -> None:
        self.handlers = handlers
        self.run_id = run_id
        self.handler_error: Exception | None = None

    def __call__(self, kind: str, **fields: object) -> None:
        if not self.handlers:
            return
        event: Event = {"event": kind, "ts": _now()}
        if self.run_id is not None:
            event["run_id"] = self.run_id
        event.update(fields)
        for handler in self.handlers:
            try:
                handler(copy.deepcopy(event))
            except Exception as e:
                self.handler_error = e
                raise


def _emitter(
    on_event: EventHandler | None,
    log_path: str | os.PathLike[str] | None,
    run_id: str | None,
    save_log: bool = False,
) -> _Emitter:
    """
    The emitter a public lookup function uses: `on_event` and a
    `JsonlEventLog` at `log_path`, each when given. The deprecated
    `save_log=True` with no `log_path` logs to `LOG_PATH`. An empty
    `log_path` counts as none.
    """
    if not log_path:
        log_path = None
    if save_log:
        warnings.warn(
            "save_log is deprecated and will be removed in a later release; "
            "pass log_path= (or on_event=) to log events",
            DeprecationWarning,
            stacklevel=3,
        )
        if log_path is None:
            log_path = LOG_PATH
    handlers: list[EventHandler] = []
    if on_event is not None:
        handlers.append(on_event)
    if log_path is not None:
        handlers.append(JsonlEventLog(log_path))
    return _Emitter(handlers, run_id)


def _now() -> str:
    """The current local time, ISO 8601 with its UTC offset, to milliseconds."""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _error_fields(error: Exception) -> dict[str, object]:
    """An error as event fields: its message and its exception type."""
    return {"error": str(error), "error_type": type(error).__name__}


def raw_url(timestamp: str, original: str) -> str:
    """
    URL of an archived page's raw copy: the `id_` form, as captured, without
    the Wayback toolbar or link rewriting.

    Args:
        timestamp (str): Capture timestamp (`YYYYMMDDhhmmss`), as in a record.
        original (str): The captured URL, as in a record's `original` field.

    Returns:
        str: `https://web.archive.org/web/<timestamp>id_/<original>`.
    """
    return f"https://web.archive.org/web/{timestamp}id_/{original}"


def _today() -> str:
    """Today's date as a Wayback timestamp (`YYYYMMDD`), read at call time."""
    return time.strftime("%Y%m%d", time.localtime())


def _resolve_filters(filters: list[str] | None, status: int | None) -> list[str]:
    """The CDX filters to send: `filters`, plus `statuscode:<status>` if given."""
    resolved = list(filters or [])
    if status is not None:
        resolved.append(f"statuscode:{status}")
    return resolved


def _is_retryable(error: Exception) -> bool:
    """Whether a lookup error is a refusal or throttle worth retrying."""
    # requests' ConnectionError covers refused connections; it does not
    # subclass the builtin one, which is caught too
    if isinstance(error, requests.ConnectionError | ConnectionError):
        return True
    # waybackpy's own retries on 500/502/503/504 ran out
    if isinstance(error, requests.exceptions.RetryError):
        return True
    response = getattr(error, "response", None)
    return isinstance(response, requests.Response) and (
        response.status_code in RETRY_STATUSES
    )


def _retry_after(error: Exception) -> float | None:
    """
    Seconds named by the `Retry-After` header of the error's response, if any,
    kept between 0 and `MAX_RETRY_AFTER`.
    """
    response = getattr(error, "response", None)
    if not isinstance(response, requests.Response):
        return None
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        seconds = (when - datetime.now(when.tzinfo)).total_seconds()
    # `nan` and `inf` parse as floats but name no wait
    if not math.isfinite(seconds):
        return None
    return min(max(seconds, 0.0), MAX_RETRY_AFTER)


def _backoff(attempt: int) -> float:
    """Exponential backoff with jitter for retry number `attempt` (from 0)."""
    return BACKOFF_SECONDS * 2**attempt + random.uniform(0, BACKOFF_SECONDS)


class _Params(TypedDict):
    """A lookup's CDX parameters, as carried on its events."""

    start_timestamp: str | None
    end_timestamp: str | None
    filters: list[str]
    collapses: list[str]


def _params(
    start_timestamp: str | None,
    end_timestamp: str | None,
    filters: list[str],
    collapses: list[str] | None,
) -> _Params:
    """A lookup's CDX parameters, as carried on its events."""
    return {
        "start_timestamp": start_timestamp,
        "end_timestamp": end_timestamp,
        "filters": filters,
        "collapses": collapses or [],
    }


class _Stats:
    """What a lookup's `lookup` event reports, filled in as the lookup runs."""

    def __init__(self) -> None:
        self.attempts = 0
        self.total_waited_s = 0.0
        self.n_snapshots = 0


@contextmanager
def _lookup_event(
    emit: _Emitter,
    function: str,
    url: str,
    user_agent: str,
    params: _Params,
) -> Generator[_Stats]:
    """Emit one `lookup` event for the lookup run inside the block, as it ends."""
    stats = _Stats()
    started_at = datetime.now().astimezone()
    start = time.perf_counter()

    def finish(**fields: object) -> None:
        emit(
            "lookup",
            function=function,
            url=url,
            user_agent=user_agent,
            **params,
            started_at=started_at.isoformat(),
            duration_seconds=round(time.perf_counter() - start, 3),
            attempts=stats.attempts,
            total_waited_s=round(stats.total_waited_s, 3),
            **fields,
        )

    try:
        yield stats
    except Exception as e:
        # A handler's own exception is not the lookup's; calling the handler
        # again to report it would raise again and hide the first
        if e is not emit.handler_error:
            finish(outcome="error", **_error_fields(e))
        raise
    finish(outcome="success", n_snapshots=stats.n_snapshots)


def _records(
    url: str,
    user_agent: str,
    params: _Params,
    min_interval: float,
    max_retries: int,
    emit: _Emitter,
    stats: _Stats,
) -> list[dict[str, str]]:
    """
    Run one lookup, retrying refusals, and emit a `request` event per attempt
    and a `retry` event per retry. `params` come from `_params`, with the end
    timestamp and filters resolved.
    """
    while True:
        stats.attempts += 1
        attempt = stats.attempts
        waited = _pacer.wait(min_interval)
        stats.total_waited_s += waited
        start = time.perf_counter()

        def request(**fields: object) -> None:
            emit(
                "request",
                url=url,
                attempt=attempt,
                waited_s=round(waited, 3),
                duration_ms=round((time.perf_counter() - start) * 1000),
                **fields,
                **params,
            )

        try:
            cdx = WaybackMachineCDXServerAPI(
                url=url,
                user_agent=user_agent,
                start_timestamp=params["start_timestamp"],
                end_timestamp=params["end_timestamp"],
                filters=params["filters"],
                collapses=params["collapses"] or None,
            )
            records = [
                {
                    "urlkey": item.urlkey,
                    "timestamp": item.timestamp,
                    "original": item.original,
                    "mimetype": item.mimetype,
                    "statuscode": item.statuscode,
                    "digest": item.digest,
                    "length": item.length,
                    "archive_url": item.archive_url,
                    "raw_url": raw_url(item.timestamp, item.original),
                }
                for item in cdx.snapshots()
            ]
        except Exception as e:
            request(outcome="error", n_records=None, **_error_fields(e))
            # Retries made so far: every attempt but the first
            if attempt - 1 >= max_retries or not _is_retryable(e):
                raise
            retry_after = _retry_after(e)
            delay = _backoff(attempt - 1) if retry_after is None else retry_after
            # The warning and the event come from one place, so they agree
            logger.warning(
                "retry %d/%d for %s in %.1fs: %s", attempt, max_retries, url, delay, e
            )
            emit(
                "retry",
                url=url,
                attempt=attempt,
                max_retries=max_retries,
                delay_s=round(delay, 3),
                retry_after=retry_after,
                **_error_fields(e),
            )
            _pacer.sleep(delay)
            stats.total_waited_s += delay
            continue
        request(outcome="ok" if records else "empty", n_records=len(records))
        stats.n_snapshots = len(records)
        return records


def get_url_records(
    url: str,
    start_timestamp: str | None = None,
    end_timestamp: str | None = None,
    user_agent: str = DEFAULT_UA,
    filters: list[str] | None = None,
    collapses: list[str] | None = None,
    status: int | None = None,
    min_interval: float = MIN_INTERVAL,
    max_retries: int = MAX_RETRIES,
    on_event: EventHandler | None = None,
    log_path: str | os.PathLike[str] | None = None,
    run_id: str | None = None,
) -> list[dict[str, str]]:
    """
    Get Wayback Machine snapshots for a URL as a list of records. Needs no
    polars.

    Args:
        url (str): URL to search.
        start_timestamp (str | None): Start timestamp for the search. `None`
            (the default) sets no lower bound.
        end_timestamp (str | None): End timestamp for the search. `None` (the
            default) means today, read when the function is called.
        user_agent (str): User agent to use for the request. Defaults to
            `wayback/<version>`.
        filters (list[str] | None): CDX filters, `[!]field:regex` (e.g.
            `statuscode:200`, `!mimetype:image/.*`), passed to waybackpy.
        collapses (list[str] | None): CDX collapses (e.g. `digest`, or
            `timestamp:8` for one capture a day), passed to waybackpy.
        status (int | None): Shortcut that adds the filter
            `statuscode:<status>`.
        min_interval (float): Seconds kept since the last lookup this process
            started, through any of the package's functions, before this one
            starts. Retries wait too.
        max_retries (int): Times to retry a lookup that was refused or
            throttled, after waybackpy's own retries, with exponential backoff
            and jitter, or the server's `Retry-After` when it sends one.
        on_event (Callable[[dict], None] | None): Called with each event the
            lookup emits: a `request` per attempt, a `retry` per retry, and a
            `lookup` as it ends. An exception it raises is not caught.
        log_path (str | os.PathLike[str] | None): Append the events to this
            JSON Lines file, as `JsonlEventLog(log_path)` would. No log is
            written unless this or `on_event` is given.
        run_id (str | None): Stamped as `run_id` on every event, to group the
            lookups of one run.

    Returns:
        list[dict[str, str]]: One dict per snapshot, with the fields in
            `RECORD_COLUMNS`; `[]` when there are none.
    """
    emit = _emitter(on_event, log_path, run_id)
    if end_timestamp is None:
        end_timestamp = _today()
    params = _params(
        start_timestamp, end_timestamp, _resolve_filters(filters, status), collapses
    )
    with _lookup_event(emit, "get_url_records", url, user_agent, params) as stats:
        return _records(url, user_agent, params, min_interval, max_retries, emit, stats)


def _snapshots(
    url: str,
    start_timestamp: str | None,
    end_timestamp: str | None,
    user_agent: str,
    verbose: bool,
    filters: list[str] | None,
    collapses: list[str] | None,
    status: int | None,
    min_interval: float,
    max_retries: int,
    emit: _Emitter,
) -> pl.DataFrame:
    """`get_url_snapshots` with its emitter resolved, for reuse by the batch."""
    import polars as pl

    if end_timestamp is None:
        end_timestamp = _today()
    params = _params(
        start_timestamp, end_timestamp, _resolve_filters(filters, status), collapses
    )
    with _lookup_event(emit, "get_url_snapshots", url, user_agent, params) as stats:
        if verbose:
            print(f"processing {url}")
            print("extracting items")
        records = _records(
            url, user_agent, params, min_interval, max_retries, emit, stats
        )

        if verbose:
            print("reshaping items")
        df = pl.DataFrame(
            records, schema=dict.fromkeys(RECORD_COLUMNS, pl.String)
        ).with_columns(
            pl.col("timestamp")
            .str.strptime(pl.Datetime, "%Y%m%d%H%M%S")
            .alias("datetime_timestamp"),
            pl.lit(datetime.now()).alias("retrieved_on"),
            pl.lit(f"{start_timestamp or ''}-{end_timestamp}").alias("retrieved_dates"),
            pl.lit(url).alias("url"),
        )
        return df.select(SNAPSHOT_COLUMNS)


def get_url_snapshots(
    url: str,
    start_timestamp: str | None = None,
    end_timestamp: str | None = None,
    user_agent: str = DEFAULT_UA,
    verbose: bool = True,
    save_log: bool = False,
    log_path: str | os.PathLike[str] | None = None,
    filters: list[str] | None = None,
    collapses: list[str] | None = None,
    status: int | None = None,
    min_interval: float = MIN_INTERVAL,
    max_retries: int = MAX_RETRIES,
    on_event: EventHandler | None = None,
    run_id: str | None = None,
) -> pl.DataFrame:
    """
    Get Wayback Machine snapshots for a given URL as a DataFrame, built on
    `get_url_records`.

    Args:
        url (str): URL to search.
        start_timestamp (str | None): Start timestamp for the search. `None`
            (the default) sets no lower bound.
        end_timestamp (str | None): End timestamp for the search. `None` (the
            default) means today, read when the function is called.
        user_agent (str): User agent to use for the request. Defaults to
            `wayback/<version>`.
        verbose (bool): Whether to print progress.
        save_log (bool): Deprecated; pass `log_path` instead. `True` logs the
            events to `log_path`, or to `LOG_PATH` (`data/wayback-runs.jsonl`,
            relative to the working directory) when no path is given, and
            raises a `DeprecationWarning`.
        log_path, on_event, run_id: As in `get_url_records`.
        filters, collapses, status, min_interval, max_retries: As in
            `get_url_records`.

    Returns:
        pl.DataFrame: One row per snapshot, with the columns in
            `SNAPSHOT_COLUMNS`. No snapshots gives zero rows with the same
            columns.
    """
    emit = _emitter(on_event, log_path, run_id, save_log=save_log)
    return _snapshots(
        url,
        start_timestamp,
        end_timestamp,
        user_agent,
        verbose,
        filters,
        collapses,
        status,
        min_interval,
        max_retries,
        emit,
    )


def get_url_list_snapshots(
    urls: list[str],
    start_timestamp: str | None = None,
    end_timestamp: str | None = None,
    output_directory: str | None = None,
    sleep_time: int = 5,
    verbose: bool = False,
    save_log: bool = False,
    log_path: str | os.PathLike[str] | None = None,
    filters: list[str] | None = None,
    collapses: list[str] | None = None,
    status: int | None = None,
    min_interval: float = MIN_INTERVAL,
    max_retries: int = MAX_RETRIES,
    on_event: EventHandler | None = None,
    run_id: str | None = None,
) -> None:
    """
    Process a list of URLs to collect Wayback snapshots and save to
    individual files with unique IDs as filenames.

    Args:
        urls (list): List of URLs to process.
        start_timestamp (str | None): Start timestamp for the search. `None`
            (the default) sets no lower bound.
        end_timestamp (str | None): End timestamp for the search. `None` (the
            default) means today, read once when the function is called, so
            every URL in the run shares it.
        output_directory (str): Directory to save the output files.
        sleep_time (int): Number of seconds to sleep after each URL, on top of
            the `min_interval` kept between lookups.
        verbose (bool): Whether to print progress.
        save_log (bool): Deprecated; as in `get_url_snapshots`.
        log_path, on_event, run_id: As in `get_url_records`. Each URL's
            lookup emits its own events, and the run ends with a `batch`
            event: settings, counts, and per-URL outcomes.
        filters, collapses, status, min_interval, max_retries: As in
            `get_url_records`, applied to every URL.

    A URL with no snapshots gets a header-only CSV.
    """
    assert output_directory is not None, "output_directory must be specified"
    emit = _emitter(on_event, log_path, run_id, save_log=save_log)
    if end_timestamp is None:
        end_timestamp = _today()
    if not os.path.exists(output_directory):
        os.makedirs(output_directory)

    started_at = datetime.now().astimezone()
    results: list[dict[str, object]] = []
    for url in tqdm.tqdm(urls):
        # Create a hash of the URL for the filename
        url_hash = hashlib.md5(url.encode()).hexdigest()
        output_path = os.path.join(output_directory, url_hash + ".csv")

        # Check if the file already exists
        if os.path.exists(output_path):
            print(f"Data for {url} already exists. Skipping.")
            results.append(
                {"url": url, "output_path": output_path, "status": "skipped"}
            )
            continue

        try:
            df = _snapshots(
                url,
                start_timestamp,
                end_timestamp,
                DEFAULT_UA,
                verbose,
                filters,
                collapses,
                status,
                min_interval,
                max_retries,
                emit,
            )
            df.write_csv(output_path)
            if verbose:
                print(f"saved to: {output_path}")
            results.append(
                {
                    "url": url,
                    "output_path": output_path,
                    "status": "saved",
                    "n_snapshots": len(df),
                }
            )
        except Exception as e:
            print(f"error on {url}: {e}")
            results.append(
                {
                    "url": url,
                    "output_path": output_path,
                    "status": "error",
                    "error": str(e),
                }
            )
        time.sleep(sleep_time)

    duration = (datetime.now().astimezone() - started_at).total_seconds()
    statuses = [r["status"] for r in results]
    emit(
        "batch",
        function="get_url_list_snapshots",
        n_urls=len(urls),
        **_params(
            start_timestamp, end_timestamp, _resolve_filters(filters, status), collapses
        ),
        output_directory=output_directory,
        sleep_time=sleep_time,
        min_interval=min_interval,
        started_at=started_at.isoformat(),
        duration_seconds=round(duration, 3),
        n_saved=statuses.count("saved"),
        n_skipped=statuses.count("skipped"),
        n_errors=statuses.count("error"),
        results=results,
    )
