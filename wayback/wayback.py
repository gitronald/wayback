import hashlib
import json
import os
import time
from datetime import datetime

import pandas as pd
import tqdm
from waybackpy import WaybackMachineCDXServerAPI

DEFAULT_UA = "Mozilla/5.0 (Windows NT 5.1; rv:40.0) Gecko/20100101 Firefox/40.0"
LOG_PATH = "data/wayback-runs.jsonl"


def _append_run_log(entry: dict[str, object]) -> None:
    """Append a run record to the JSONL run log at LOG_PATH."""
    log_dir = os.path.dirname(LOG_PATH)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _count_snapshots(df: pd.DataFrame) -> int:
    """Count snapshot rows, excluding the placeholder row of an empty result."""
    if "timestamp" not in df.columns:
        return 0
    return int(df["timestamp"].notna().sum())


def get_url_snapshots(
    url: str,
    start_timestamp: str = "20000101",
    end_timestamp: str = time.strftime("%Y%m%d", time.localtime()),
    user_agent: str = DEFAULT_UA,
    verbose: bool = True,
    save_log: bool = True,
) -> pd.DataFrame:
    """
    Get Wayback Machine snapshots for a given URL.

    Args:
        url (str): URL to search.
        start_timestamp (str): Start timestamp for Wayback Machine search.
        end_timestamp (str): End timestamp for Wayback Machine search.
        user_agent (str): User agent to use for the request.
        verbose (bool): Whether to print progress.
        save_log (bool): Whether to append a record of the run and its
            outcome to `data/wayback-runs.jsonl`.

    Returns:
        pd.DataFrame: DataFrame containing the snapshots.
    """
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
        df = pd.DataFrame(items) if items else pd.DataFrame({}, index=[0])
        df["archive_url"] = None if "archive_url" not in df else df["archive_url"]
        df["retrieved_on"] = pd.Timestamp.now()
        df["retrieved_dates"] = str(start_timestamp) + "-" + str(end_timestamp)
        df["url"] = url
        df = df[["url"] + list(df.drop(columns=["url"]).columns)]
    except Exception as e:
        if save_log:
            duration = (datetime.now().astimezone() - started_at).total_seconds()
            log_entry.update(
                duration_seconds=round(duration, 3),
                outcome="error",
                error=str(e),
            )
            _append_run_log(log_entry)
        raise

    if save_log:
        duration = (datetime.now().astimezone() - started_at).total_seconds()
        log_entry.update(
            duration_seconds=round(duration, 3),
            outcome="success",
            n_snapshots=_count_snapshots(df),
        )
        _append_run_log(log_entry)

    return df


def get_url_list_snapshots(
    urls: list[str],
    start_timestamp: str,
    end_timestamp: str,
    output_directory: str | None = None,
    sleep_time: int = 5,
    verbose: bool = False,
    save_log: bool = True,
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
            counts, and per-URL outcomes — to `data/wayback-runs.jsonl`.
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
                    "n_snapshots": _count_snapshots(df),
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
            }
        )
