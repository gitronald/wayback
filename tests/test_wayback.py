"""Tests for wayback. No test touches the network: the CDX client is faked."""

import csv
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from waybackpy.cdx_snapshot import CDXSnapshot

import wayback
from wayback import wayback as wb


def _snapshot(timestamp: str, original: str = "https://example.com/") -> CDXSnapshot:
    return CDXSnapshot(
        {
            "urlkey": "com,example)/",
            "timestamp": timestamp,
            "original": original,
            "mimetype": "text/html",
            "statuscode": "200",
            "digest": "A" * 32,
            "length": "1234",
        }
    )


class FakeCDX:
    """Stand-in for WaybackMachineCDXServerAPI that records its arguments."""

    calls: list[dict[str, object]] = []
    rows: list[CDXSnapshot] = []
    refuse: set[str] = set()

    def __init__(self, **kwargs: object) -> None:
        FakeCDX.calls.append(kwargs)
        self.url = kwargs["url"]

    def snapshots(self) -> Iterator[CDXSnapshot]:
        if self.url in FakeCDX.refuse:
            raise ConnectionError("refused")
        yield from FakeCDX.rows


@pytest.fixture
def fake_cdx(monkeypatch: pytest.MonkeyPatch) -> type[FakeCDX]:
    FakeCDX.calls = []
    FakeCDX.rows = []
    FakeCDX.refuse = set()
    monkeypatch.setattr(wb, "WaybackMachineCDXServerAPI", FakeCDX)
    return FakeCDX


def _skip_url(output_directory: Path, url: str) -> None:
    """Pre-create a URL's output file so the batch skips it."""
    output_directory.mkdir(exist_ok=True)
    url_hash = hashlib.md5(url.encode()).hexdigest()
    (output_directory / f"{url_hash}.csv").write_text("url\n")


def test_public_api() -> None:
    assert callable(wayback.get_url_snapshots)
    assert callable(wayback.get_url_list_snapshots)
    assert wayback.SNAPSHOT_COLUMNS == wb.SNAPSHOT_COLUMNS


# Run log


def test_defaults_write_no_log(tmp_path, monkeypatch, fake_cdx) -> None:
    monkeypatch.chdir(tmp_path)
    wayback.get_url_snapshots("example.com", verbose=False)
    _skip_url(tmp_path / "snapshots", "example.com")
    wayback.get_url_list_snapshots(
        ["example.com"],
        "20200101",
        "20201231",
        output_directory=str(tmp_path / "snapshots"),
        sleep_time=0,
    )
    assert not (tmp_path / "data").exists()


def test_save_log_writes_to_log_path(tmp_path, monkeypatch, fake_cdx) -> None:
    monkeypatch.chdir(tmp_path)
    fake_cdx.rows = [_snapshot("20200101000000")]
    log_path = tmp_path / "logs" / "runs.jsonl"
    wayback.get_url_snapshots(
        "example.com", verbose=False, save_log=True, log_path=str(log_path)
    )
    entry = json.loads(log_path.read_text())
    assert entry["outcome"] == "success"
    assert entry["n_snapshots"] == 1
    assert not (tmp_path / "data").exists()


def test_save_log_without_path_writes_log_path_default(
    tmp_path, monkeypatch, fake_cdx
) -> None:
    monkeypatch.chdir(tmp_path)
    wayback.get_url_snapshots("example.com", verbose=False, save_log=True)
    entry = json.loads((tmp_path / wb.LOG_PATH).read_text())
    assert entry["function"] == "get_url_snapshots"
    assert entry["n_snapshots"] == 0


def test_save_log_records_errors(tmp_path, monkeypatch) -> None:
    class Refused:
        def __init__(self, **kwargs: object) -> None:
            pass

        def snapshots(self) -> Iterator[CDXSnapshot]:
            raise ConnectionError("refused")

    monkeypatch.setattr(wb, "WaybackMachineCDXServerAPI", Refused)
    log_path = tmp_path / "runs.jsonl"
    with pytest.raises(ConnectionError):
        wayback.get_url_snapshots(
            "example.com", verbose=False, save_log=True, log_path=str(log_path)
        )
    entry = json.loads(log_path.read_text())
    assert entry["outcome"] == "error"
    assert entry["error"] == "refused"


def test_batch_run_log(tmp_path) -> None:
    url = "example.com"
    output_directory = tmp_path / "snapshots"
    _skip_url(output_directory, url)
    log_path = tmp_path / "runs.jsonl"

    wayback.get_url_list_snapshots(
        urls=[url],
        start_timestamp="20200101",
        end_timestamp="20201231",
        output_directory=str(output_directory),
        sleep_time=0,
        save_log=True,
        log_path=str(log_path),
    )

    log_lines = log_path.read_text().splitlines()
    assert len(log_lines) == 1
    entry = json.loads(log_lines[0])
    assert entry["function"] == "get_url_list_snapshots"
    assert entry["n_urls"] == 1
    assert entry["n_saved"] == 0
    assert entry["n_skipped"] == 1
    assert entry["n_errors"] == 0
    assert entry["results"][0]["status"] == "skipped"
    assert entry["started_at"] and entry["duration_seconds"] >= 0


# Request arguments


def test_default_user_agent_names_the_package(fake_cdx) -> None:
    assert wb.DEFAULT_UA.startswith("wayback/")
    wayback.get_url_snapshots("example.com", verbose=False)
    assert fake_cdx.calls[0]["user_agent"] == wb.DEFAULT_UA


def test_user_agent_without_metadata(monkeypatch) -> None:
    def missing(name: str) -> str:
        raise wb.PackageNotFoundError(name)

    monkeypatch.setattr(wb, "version", missing)
    assert wb._default_user_agent() == "wayback"


def test_end_timestamp_resolves_at_call_time(monkeypatch, fake_cdx) -> None:
    days = iter(["20261106", "20261107"])
    monkeypatch.setattr(wb, "_today", lambda: next(days))
    wayback.get_url_snapshots("example.com", verbose=False)
    wayback.get_url_snapshots("example.com", verbose=False)
    assert [c["end_timestamp"] for c in fake_cdx.calls] == ["20261106", "20261107"]


def test_start_timestamp_defaults_to_no_bound(fake_cdx) -> None:
    df = wayback.get_url_snapshots("example.com", verbose=False)
    assert fake_cdx.calls[0]["start_timestamp"] is None
    assert list(df.columns) == wb.SNAPSHOT_COLUMNS


# Result shape


def test_snapshot_columns(fake_cdx) -> None:
    fake_cdx.rows = [_snapshot("20200101000000"), _snapshot("20200102000000")]
    df = wayback.get_url_snapshots("example.com", "20200101", "20201231", verbose=False)
    assert list(df.columns) == wb.SNAPSHOT_COLUMNS
    assert len(df) == 2
    assert df["url"].tolist() == ["example.com", "example.com"]
    assert df["retrieved_dates"].iloc[0] == "20200101-20201231"


def test_empty_result_has_no_rows(fake_cdx) -> None:
    df = wayback.get_url_snapshots("example.com", verbose=False)
    assert len(df) == 0
    assert list(df.columns) == wb.SNAPSHOT_COLUMNS


def test_batch_writes_header_only_csv_for_empty(tmp_path, fake_cdx) -> None:
    url = "example.com"
    wayback.get_url_list_snapshots(
        [url],
        "20200101",
        "20201231",
        output_directory=str(tmp_path),
        sleep_time=0,
    )
    url_hash = hashlib.md5(url.encode()).hexdigest()
    with open(tmp_path / f"{url_hash}.csv", newline="") as f:
        rows = list(csv.reader(f))
    assert rows == [wb.SNAPSHOT_COLUMNS]


def test_batch_records_saved_and_error(tmp_path, fake_cdx) -> None:
    fake_cdx.rows = [_snapshot("20200101000000")]
    fake_cdx.refuse = {"b.example"}
    log_path = tmp_path / "runs.jsonl"
    wayback.get_url_list_snapshots(
        ["a.example", "b.example"],
        "20200101",
        "20201231",
        output_directory=str(tmp_path / "out"),
        sleep_time=0,
        save_log=True,
        log_path=str(log_path),
    )
    entry = json.loads(log_path.read_text())
    assert [r["status"] for r in entry["results"]] == ["saved", "error"]
    assert entry["results"][0]["n_snapshots"] == 1
