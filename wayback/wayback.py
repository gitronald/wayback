from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import threading
import time
from collections.abc import Callable
from datetime import datetime
from email.utils import parsedate_to_datetime
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

import requests
import tqdm
from waybackpy import WaybackMachineCDXServerAPI

if TYPE_CHECKING:
    import pandas as pd

logger = logging.getLogger("wayback")


def _default_user_agent() -> str:
    """Name the package and its version, e.g. `wayback/0.2.0`."""
    try:
        return f"wayback/{version('wayback')}"
    except PackageNotFoundError:
        return "wayback"


DEFAULT_UA = _default_user_agent()
LOG_PATH = "data/wayback-runs.jsonl"

# Seconds kept between any two lookups the package starts in a process
MIN_INTERVAL = 5.0
# Retries of a refused or throttled lookup, on top of waybackpy's own
MAX_RETRIES = 3
# Base of the exponential backoff between retries, in seconds
BACKOFF_SECONDS = 5.0
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

    def wait(self, min_interval: float) -> None:
        """Sleep until `min_interval` seconds have passed since the last lookup."""
        with self._lock:
            now = self.clock()
            if self._last is not None:
                gap = self._last + min_interval - now
                if gap > 0:
                    self.sleep(gap)
                    now = self.clock()
            self._last = now


_pacer = _Pacer()


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
    """Seconds named by the `Retry-After` header of the error's response, if any."""
    response = getattr(error, "response", None)
    if not isinstance(response, requests.Response):
        return None
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max((when - datetime.now(when.tzinfo)).total_seconds(), 0.0)


def _backoff(attempt: int) -> float:
    """Exponential backoff with jitter for retry number `attempt` (from 0)."""
    return BACKOFF_SECONDS * 2**attempt + random.uniform(0, BACKOFF_SECONDS)


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
) -> list[dict[str, str]]:
    """
    Get Wayback Machine snapshots for a URL as a list of records. Needs no
    pandas.

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

    Returns:
        list[dict[str, str]]: One dict per snapshot, with the fields in
            `RECORD_COLUMNS`; `[]` when there are none.
    """
    if end_timestamp is None:
        end_timestamp = _today()
    resolved_filters = _resolve_filters(filters, status)

    attempt = 0
    while True:
        _pacer.wait(min_interval)
        try:
            cdx = WaybackMachineCDXServerAPI(
                url=url,
                user_agent=user_agent,
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp,
                filters=resolved_filters,
                collapses=collapses,
            )
            return [
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
            if attempt >= max_retries or not _is_retryable(e):
                raise
            retry_after = _retry_after(e)
            delay = _backoff(attempt) if retry_after is None else retry_after
            attempt += 1
            logger.warning(
                "retry %d/%d for %s in %.1fs: %s", attempt, max_retries, url, delay, e
            )
            _pacer.sleep(delay)


def _append_run_log(entry: dict[str, object], log_path: str | None = None) -> None:
    """Append a run record to the JSONL run log at `log_path` (or LOG_PATH)."""
    path = log_path or LOG_PATH
    log_dir = os.path.dirname(path)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def get_url_snapshots(
    url: str,
    start_timestamp: str | None = None,
    end_timestamp: str | None = None,
    user_agent: str = DEFAULT_UA,
    verbose: bool = True,
    save_log: bool = False,
    log_path: str | None = None,
    filters: list[str] | None = None,
    collapses: list[str] | None = None,
    status: int | None = None,
    min_interval: float = MIN_INTERVAL,
    max_retries: int = MAX_RETRIES,
) -> pd.DataFrame:
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
        save_log (bool): Whether to append a record of the run and its
            outcome to the JSONL run log.
        log_path (str | None): Where to write the run log when `save_log` is
            set. `None` writes to `LOG_PATH` (`data/wayback-runs.jsonl`,
            relative to the working directory).
        filters, collapses, status, min_interval, max_retries: As in
            `get_url_records`.

    Returns:
        pd.DataFrame: One row per snapshot, with the columns in
            `SNAPSHOT_COLUMNS`. No snapshots gives zero rows with the same
            columns.
    """
    import pandas as pd

    if end_timestamp is None:
        end_timestamp = _today()
    started_at = datetime.now().astimezone()
    log_entry: dict[str, object] = {
        "function": "get_url_snapshots",
        "url": url,
        "start_timestamp": start_timestamp,
        "end_timestamp": end_timestamp,
        "user_agent": user_agent,
        "filters": _resolve_filters(filters, status),
        "collapses": collapses or [],
        "started_at": started_at.isoformat(),
    }

    try:
        if verbose:
            print(f"processing {url}")
            print("extracting items")
        records = get_url_records(
            url,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            user_agent=user_agent,
            filters=filters,
            collapses=collapses,
            status=status,
            min_interval=min_interval,
            max_retries=max_retries,
        )

        if verbose:
            print("reshaping items")
        df = pd.DataFrame(records, columns=RECORD_COLUMNS)
        df["datetime_timestamp"] = pd.to_datetime(
            df["timestamp"], format="%Y%m%d%H%M%S"
        )
        df["retrieved_on"] = pd.Timestamp.now()
        df["retrieved_dates"] = f"{start_timestamp or ''}-{end_timestamp}"
        df["url"] = url
        df = df[SNAPSHOT_COLUMNS]
    except Exception as e:
        if save_log:
            duration = (datetime.now().astimezone() - started_at).total_seconds()
            log_entry.update(
                duration_seconds=round(duration, 3),
                outcome="error",
                error=str(e),
            )
            _append_run_log(log_entry, log_path)
        raise

    if save_log:
        duration = (datetime.now().astimezone() - started_at).total_seconds()
        log_entry.update(
            duration_seconds=round(duration, 3),
            outcome="success",
            n_snapshots=len(df),
        )
        _append_run_log(log_entry, log_path)

    return df


def get_url_list_snapshots(
    urls: list[str],
    start_timestamp: str,
    end_timestamp: str,
    output_directory: str | None = None,
    sleep_time: int = 5,
    verbose: bool = False,
    save_log: bool = False,
    log_path: str | None = None,
    filters: list[str] | None = None,
    collapses: list[str] | None = None,
    status: int | None = None,
    min_interval: float = MIN_INTERVAL,
    max_retries: int = MAX_RETRIES,
) -> None:
    """
    Process a list of URLs to collect Wayback snapshots and save to
    individual files with unique IDs as filenames.

    Args:
        urls (list): List of URLs to process.
        start_timestamp (str): Start timestamp for Wayback Machine search.
        end_timestamp (str): End timestamp for Wayback Machine search.
        output_directory (str): Directory to save the output files.
        sleep_time (int): Number of seconds to sleep after each URL, on top of
            the `min_interval` kept between lookups.
        verbose (bool): Whether to print progress.
        save_log (bool): Whether to append a record of the run — settings,
            counts, and per-URL outcomes — to the JSONL run log.
        log_path (str | None): Where to write the run log when `save_log` is
            set. `None` writes to `LOG_PATH` (`data/wayback-runs.jsonl`,
            relative to the working directory).
        filters, collapses, status, min_interval, max_retries: As in
            `get_url_records`, applied to every URL.

    A URL with no snapshots gets a header-only CSV.
    """
    assert output_directory is not None, "output_directory must be specified"
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
            # The inner call logs nothing itself; its outcome is recorded in
            # this run's per-URL results instead
            df = get_url_snapshots(
                url,
                start_timestamp,
                end_timestamp,
                verbose=verbose,
                save_log=False,
                filters=filters,
                collapses=collapses,
                status=status,
                min_interval=min_interval,
                max_retries=max_retries,
            )
            df.to_csv(output_path, index=False)
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

    if save_log:
        duration = (datetime.now().astimezone() - started_at).total_seconds()
        statuses = [r["status"] for r in results]
        _append_run_log(
            {
                "function": "get_url_list_snapshots",
                "n_urls": len(urls),
                "start_timestamp": start_timestamp,
                "end_timestamp": end_timestamp,
                "filters": _resolve_filters(filters, status),
                "collapses": collapses or [],
                "output_directory": output_directory,
                "sleep_time": sleep_time,
                "min_interval": min_interval,
                "started_at": started_at.isoformat(),
                "duration_seconds": round(duration, 3),
                "n_saved": statuses.count("saved"),
                "n_skipped": statuses.count("skipped"),
                "n_errors": statuses.count("error"),
                "results": results,
            },
            log_path,
        )
