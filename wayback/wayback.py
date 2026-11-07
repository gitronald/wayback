import hashlib
import json
import os
import time
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version

import pandas as pd
import tqdm
from waybackpy import WaybackMachineCDXServerAPI


def _default_user_agent() -> str:
    """Name the package and its version, e.g. `wayback/0.2.0`."""
    try:
        return f"wayback/{version('wayback')}"
    except PackageNotFoundError:
        return "wayback"


DEFAULT_UA = _default_user_agent()
LOG_PATH = "data/wayback-runs.jsonl"

# Columns of a snapshot frame, in order; an empty result carries them too
CDX_COLUMNS = [
    "urlkey",
    "timestamp",
    "datetime_timestamp",
    "original",
    "mimetype",
    "statuscode",
    "digest",
    "length",
    "archive_url",
]
SNAPSHOT_COLUMNS = ["url", *CDX_COLUMNS, "retrieved_on", "retrieved_dates"]


def _today() -> str:
    """Today's date as a Wayback timestamp (`YYYYMMDD`), read at call time."""
    return time.strftime("%Y%m%d", time.localtime())


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
) -> pd.DataFrame:
    """
    Get Wayback Machine snapshots for a given URL.

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

    Returns:
        pd.DataFrame: One row per snapshot, with the columns in
            `SNAPSHOT_COLUMNS`. No snapshots gives zero rows with the same
            columns.
    """
    if end_timestamp is None:
        end_timestamp = _today()
    started_at = datetime.now().astimezone()
    log_entry: dict[str, object] = {
        "function": "get_url_snapshots",
        "url": url,
        "start_timestamp": start_timestamp,
        "end_timestamp": end_timestamp,
        "user_agent": user_agent,
        "started_at": started_at.isoformat(),
    }

    try:
        if verbose:
            print(f"processing {url}")
        cdx = WaybackMachineCDXServerAPI(
            url=url,
            user_agent=user_agent,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
        )

        if verbose:
            print("extracting items")
        items = [vars(item) for item in cdx.snapshots()]

        if verbose:
            print("reshaping items")
        df = pd.DataFrame(items, columns=CDX_COLUMNS)
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
) -> None:
    """
    Process a list of URLs to collect Wayback snapshots and save to
    individual files with unique IDs as filenames.

    Args:
        urls (list): List of URLs to process.
        start_timestamp (str): Start timestamp for Wayback Machine search.
        end_timestamp (str): End timestamp for Wayback Machine search.
        output_directory (str): Directory to save the output files.
        sleep_time (int): Number of seconds to sleep between requests.
        verbose (bool): Whether to print progress.
        save_log (bool): Whether to append a record of the run — settings,
            counts, and per-URL outcomes — to the JSONL run log.
        log_path (str | None): Where to write the run log when `save_log` is
            set. `None` writes to `LOG_PATH` (`data/wayback-runs.jsonl`,
            relative to the working directory).

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
                url, start_timestamp, end_timestamp, verbose=verbose, save_log=False
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
                "output_directory": output_directory,
                "sleep_time": sleep_time,
                "started_at": started_at.isoformat(),
                "duration_seconds": round(duration, 3),
                "n_saved": statuses.count("saved"),
                "n_skipped": statuses.count("skipped"),
                "n_errors": statuses.count("error"),
                "results": results,
            },
            log_path,
        )
