"""Tests for wayback. No test touches the network: the CDX client is faked."""

import csv
import hashlib
import json
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path

import polars as pl
import pytest
import requests
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
    # Raised one per lookup, in order, before any lookup succeeds
    errors: list[Exception] = []

    def __init__(self, **kwargs: object) -> None:
        FakeCDX.calls.append(kwargs)
        self.url = kwargs["url"]

    def snapshots(self) -> Iterator[CDXSnapshot]:
        if self.url in FakeCDX.refuse:
            raise ConnectionError("refused")
        if FakeCDX.errors:
            raise FakeCDX.errors.pop(0)
        yield from FakeCDX.rows


class FakeClock:
    """A clock that only moves when the code under test sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """Give every test a fresh pacer on a fake clock, so nothing really sleeps."""
    fake = FakeClock()
    monkeypatch.setattr(wb, "_pacer", wb._Pacer(clock=fake.time, sleep=fake.sleep))
    return fake


@pytest.fixture
def fake_cdx(monkeypatch: pytest.MonkeyPatch) -> type[FakeCDX]:
    FakeCDX.calls = []
    FakeCDX.rows = []
    FakeCDX.refuse = set()
    FakeCDX.errors = []
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
    assert callable(wayback.get_url_records)
    assert callable(wayback.raw_url)


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


def test_save_log_records_errors(tmp_path, fake_cdx) -> None:
    fake_cdx.refuse = {"example.com"}
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
    wayback.get_url_snapshots("example.com", verbose=False)
    assert fake_cdx.calls[0]["start_timestamp"] is None


# Result shape


def test_snapshot_columns(fake_cdx) -> None:
    fake_cdx.rows = [_snapshot("20200101000000"), _snapshot("20200102000000")]
    df = wayback.get_url_snapshots("example.com", "20200101", "20201231", verbose=False)
    assert list(df.columns) == wb.SNAPSHOT_COLUMNS
    assert len(df) == 2
    assert df["url"].to_list() == ["example.com", "example.com"]
    assert df["retrieved_dates"][0] == "20200101-20201231"


def test_empty_result_has_no_rows(fake_cdx) -> None:
    df = wayback.get_url_snapshots("example.com", verbose=False)
    assert len(df) == 0
    assert list(df.columns) == wb.SNAPSHOT_COLUMNS
    assert df.schema["datetime_timestamp"] == pl.Datetime
    assert df.schema["retrieved_on"] == pl.Datetime


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


# Filters and collapses


def test_filters_collapses_and_status_reach_cdx(fake_cdx) -> None:
    filters = ["!mimetype:image/.*"]
    wayback.get_url_snapshots(
        "example.com",
        verbose=False,
        filters=filters,
        collapses=["digest"],
        status=200,
    )
    call = fake_cdx.calls[0]
    assert call["filters"] == ["!mimetype:image/.*", "statuscode:200"]
    assert call["collapses"] == ["digest"]
    assert filters == ["!mimetype:image/.*"]  # the caller's list is not changed


def test_no_filters_by_default(fake_cdx) -> None:
    wayback.get_url_records("example.com", min_interval=0)
    assert fake_cdx.calls[0]["filters"] == []
    assert fake_cdx.calls[0]["collapses"] is None


def test_batch_passes_filters(tmp_path, fake_cdx) -> None:
    wayback.get_url_list_snapshots(
        ["example.com"],
        "20200101",
        "20201231",
        output_directory=str(tmp_path),
        sleep_time=0,
        status=200,
        collapses=["digest"],
    )
    assert fake_cdx.calls[0]["filters"] == ["statuscode:200"]
    assert fake_cdx.calls[0]["collapses"] == ["digest"]


# Records and raw copies


def test_raw_url() -> None:
    assert (
        wayback.raw_url("20200101000000", "https://example.com/a?b=1")
        == "https://web.archive.org/web/20200101000000id_/https://example.com/a?b=1"
    )


def test_records_empty(fake_cdx) -> None:
    assert wayback.get_url_records("example.com") == []


def test_records_rows(fake_cdx) -> None:
    fake_cdx.rows = [_snapshot("20200101000000")]
    (record,) = wayback.get_url_records("example.com")
    assert list(record) == wb.RECORD_COLUMNS
    assert record["timestamp"] == "20200101000000"
    assert record["raw_url"] == wayback.raw_url(
        "20200101000000", "https://example.com/"
    )
    assert record["archive_url"] == (
        "https://web.archive.org/web/20200101000000/https://example.com/"
    )
    assert all(isinstance(v, str) for v in record.values())


def test_snapshots_match_records(fake_cdx) -> None:
    fake_cdx.rows = [_snapshot("20200101000000"), _snapshot("20200102000000")]
    records = wayback.get_url_records("example.com")
    df = wayback.get_url_snapshots("example.com", verbose=False)
    assert df.select(wb.RECORD_COLUMNS).to_dicts() == records
    assert str(df["datetime_timestamp"][1]) == "2020-01-02 00:00:00"


def test_records_import_without_polars() -> None:
    script = textwrap.dedent(
        """
        import sys
        sys.modules["polars"] = None  # any import of polars now fails

        import wayback
        from wayback import wayback as wb

        class Fake:
            def __init__(self, **kwargs):
                pass

            def snapshots(self):
                return iter([])

        wb.WaybackMachineCDXServerAPI = Fake
        assert wayback.get_url_records("example.com", min_interval=0) == []
        try:
            wayback.get_url_snapshots("example.com", verbose=False)
        except ImportError:
            print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "ok"


# Pacing


def test_pacer_keeps_the_gap(clock, fake_cdx) -> None:
    wayback.get_url_records("example.com", min_interval=5)
    assert clock.sleeps == []  # the first lookup does not wait
    clock.now += 2
    wayback.get_url_records("example.com", min_interval=5)
    assert clock.sleeps == [pytest.approx(3)]
    clock.now += 10
    wayback.get_url_records("example.com", min_interval=5)
    assert clock.sleeps == [pytest.approx(3)]  # the gap had already passed


def test_pacer_spans_functions(clock, fake_cdx) -> None:
    wayback.get_url_records("example.com")
    wayback.get_url_snapshots("example.com", verbose=False)
    assert clock.sleeps == [pytest.approx(wb.MIN_INTERVAL)]


# Retries


@pytest.fixture
def flaky(monkeypatch: pytest.MonkeyPatch, fake_cdx: type[FakeCDX]) -> type[FakeCDX]:
    """The fake CDX client with one snapshot to return and no backoff jitter."""
    fake_cdx.rows = [_snapshot("20200101000000")]
    monkeypatch.setattr(wb.random, "uniform", lambda a, b: 0.0)
    return fake_cdx


def _http_error(status: int, retry_after: str | None = None) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    if retry_after is not None:
        response.headers["Retry-After"] = retry_after
    return requests.HTTPError(response=response)


def test_refused_then_success_retries_once(clock, flaky, caplog) -> None:
    flaky.errors = [requests.ConnectionError("[Errno 111] Connection refused")]
    records = wayback.get_url_records("example.com", min_interval=0)
    assert len(records) == 1
    assert len(flaky.calls) == 2
    assert clock.sleeps == [wb.BACKOFF_SECONDS]
    assert "retry 1/3 for example.com" in caplog.text


def test_retries_give_up_after_max_retries(clock, flaky) -> None:
    flaky.errors = [requests.ConnectionError("refused") for _ in range(3)]
    with pytest.raises(requests.ConnectionError):
        wayback.get_url_records("example.com", min_interval=0, max_retries=2)
    assert len(flaky.calls) == 3
    assert clock.sleeps == [wb.BACKOFF_SECONDS, wb.BACKOFF_SECONDS * 2]


def test_retry_after_is_honored(clock, flaky) -> None:
    flaky.errors = [_http_error(429, retry_after="42")]
    wayback.get_url_records("example.com", min_interval=0)
    assert clock.sleeps == [42.0]


def test_retry_after_http_date(clock, flaky) -> None:
    flaky.errors = [_http_error(503, retry_after="Wed, 21 Oct 2015 07:28:00 GMT")]
    wayback.get_url_records("example.com", min_interval=0)
    assert clock.sleeps == [0.0]  # a date in the past means retry now


def test_exhausted_waybackpy_retries_are_retried(clock, flaky) -> None:
    flaky.errors = [requests.exceptions.RetryError("too many 503 error responses")]
    assert len(wayback.get_url_records("example.com", min_interval=0)) == 1
    assert len(flaky.calls) == 2


@pytest.mark.parametrize("retry_after", ["soon", "nan", "inf", "1e999"])
def test_unparseable_retry_after_falls_back_to_backoff(
    clock, flaky, retry_after
) -> None:
    flaky.errors = [_http_error(429, retry_after=retry_after)]
    wayback.get_url_records("example.com", min_interval=0)
    assert clock.sleeps == [wb.BACKOFF_SECONDS]


@pytest.mark.parametrize("error", [ValueError("bad filter"), _http_error(404)])
def test_other_errors_are_not_retried(clock, flaky, error) -> None:
    flaky.errors = [error]
    with pytest.raises(type(error)):
        wayback.get_url_records("example.com", min_interval=0)
    assert len(flaky.calls) == 1
    assert clock.sleeps == []
