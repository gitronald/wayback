"""Tests for wayback. No test touches the network: the CDX client is faked."""

import csv
import hashlib
import json
import subprocess
import sys
import textwrap
import threading
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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


# Run log and events


def _events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _kinds(events: list[dict[str, object]]) -> list[object]:
    return [e["event"] for e in events]


def test_defaults_write_no_log(tmp_path, monkeypatch, fake_cdx) -> None:
    monkeypatch.chdir(tmp_path)
    wayback.get_url_records("example.com")
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


def test_log_path_alone_turns_the_log_on(tmp_path, monkeypatch, fake_cdx) -> None:
    monkeypatch.chdir(tmp_path)
    fake_cdx.rows = [_snapshot("20200101000000")]
    log_path = tmp_path / "logs" / "runs.jsonl"
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # no deprecation warning without save_log
        wayback.get_url_snapshots("example.com", verbose=False, log_path=log_path)
    events = _events(log_path)
    assert _kinds(events) == ["request", "lookup"]
    lookup = events[-1]
    assert lookup["function"] == "get_url_snapshots"
    assert lookup["outcome"] == "success"
    assert lookup["n_snapshots"] == 1
    assert not (tmp_path / "data").exists()


def test_records_log_path(tmp_path, fake_cdx) -> None:
    log_path = tmp_path / "runs.jsonl"
    wayback.get_url_records("example.com", log_path=str(log_path))
    events = _events(log_path)
    assert _kinds(events) == ["request", "lookup"]
    assert events[0]["outcome"] == "empty"
    assert events[1]["function"] == "get_url_records"
    assert events[1]["n_snapshots"] == 0


def test_save_log_is_deprecated(tmp_path, fake_cdx) -> None:
    log_path = tmp_path / "runs.jsonl"
    with pytest.warns(DeprecationWarning, match="save_log is deprecated"):
        wayback.get_url_snapshots(
            "example.com", verbose=False, save_log=True, log_path=str(log_path)
        )
    assert _kinds(_events(log_path)) == ["request", "lookup"]


def test_save_log_without_path_writes_log_path_default(
    tmp_path, monkeypatch, fake_cdx
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.warns(DeprecationWarning):
        wayback.get_url_snapshots("example.com", verbose=False, save_log=True)
    lookup = _events(tmp_path / wb.LOG_PATH)[-1]
    assert lookup["function"] == "get_url_snapshots"
    assert lookup["n_snapshots"] == 0


def test_lookup_event_keeps_the_run_log_fields(fake_cdx) -> None:
    events: list[dict[str, object]] = []
    wayback.get_url_snapshots(
        "example.com",
        "20200101",
        "20201231",
        verbose=False,
        status=200,
        collapses=["digest"],
        on_event=events.append,
    )
    lookup = events[-1]
    # Every field the v0.2.0 run log wrote, plus the new ones
    assert {
        "function",
        "url",
        "start_timestamp",
        "end_timestamp",
        "user_agent",
        "filters",
        "collapses",
        "started_at",
        "duration_seconds",
        "outcome",
        "n_snapshots",
        "attempts",
        "total_waited_s",
        "event",
        "ts",
    } <= set(lookup)
    assert lookup["filters"] == ["statuscode:200"]
    assert lookup["collapses"] == ["digest"]
    assert lookup["attempts"] == 1
    assert "run_id" not in lookup


def test_request_event(fake_cdx) -> None:
    fake_cdx.rows = [_snapshot("20200101000000"), _snapshot("20210101000000")]
    events: list[dict[str, object]] = []
    wayback.get_url_records(
        "example.com", "20200101", "20211231", on_event=events.append
    )
    request = events[0]
    assert request["event"] == "request"
    assert request["url"] == "example.com"
    assert request["attempt"] == 1
    assert request["waited_s"] == 0
    assert isinstance(request["duration_ms"], int) and request["duration_ms"] >= 0
    assert request["outcome"] == "ok"
    assert request["n_records"] == 2
    assert request["start_timestamp"] == "20200101"
    assert request["end_timestamp"] == "20211231"
    assert request["filters"] == [] and request["collapses"] == []
    assert "error" not in request


def test_errors_are_logged_and_raised(fake_cdx) -> None:
    fake_cdx.errors = [ValueError("bad filter")]
    events: list[dict[str, object]] = []
    with pytest.raises(ValueError):
        wayback.get_url_records("example.com", on_event=events.append)
    assert _kinds(events) == ["request", "lookup"]
    for event in events:
        assert event["error"] == "bad filter"
        assert event["error_type"] == "ValueError"
    assert events[0]["outcome"] == "error"
    assert events[0]["n_records"] is None
    assert events[1]["outcome"] == "error"


def test_pacer_wait_is_reported(clock, fake_cdx) -> None:
    events: list[dict[str, object]] = []
    wayback.get_url_records("example.com", min_interval=5)
    clock.now += 2
    wayback.get_url_records("example.com", min_interval=5, on_event=events.append)
    assert events[0]["waited_s"] == pytest.approx(3)
    assert events[1]["total_waited_s"] == pytest.approx(3)


def test_run_id_is_stamped_on_every_event(tmp_path, fake_cdx) -> None:
    events: list[dict[str, object]] = []
    wayback.get_url_records("example.com", on_event=events.append, run_id="r1")
    assert {e["run_id"] for e in events} == {"r1"}


def test_on_event_and_log_path_both_get_events(tmp_path, fake_cdx) -> None:
    events: list[dict[str, object]] = []
    log_path = tmp_path / "runs.jsonl"
    wayback.get_url_records("example.com", on_event=events.append, log_path=log_path)
    assert events == _events(log_path)


def test_jsonl_event_log_round_trip(tmp_path) -> None:
    path = tmp_path / "nested" / "events.jsonl"
    log = wayback.JsonlEventLog(path, run_id="r2")
    log({"event": "request", "url": "a"})
    log({"event": "lookup", "url": "a", "run_id": "own"})
    assert _events(path) == [
        {"event": "request", "url": "a", "run_id": "r2"},
        {"event": "lookup", "url": "a", "run_id": "own"},
    ]


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
        log_path=str(log_path),
    )

    events = _events(log_path)
    assert _kinds(events) == ["batch"]
    entry = events[0]
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


def test_list_timestamps_default_and_share_one_day(
    tmp_path, monkeypatch, fake_cdx
) -> None:
    days = iter(["20261106", "20261107"])
    monkeypatch.setattr(wb, "_today", lambda: next(days))
    wayback.get_url_list_snapshots(
        ["example.com", "example.org"],
        output_directory=str(tmp_path / "snapshots"),
        sleep_time=0,
    )
    assert [c["start_timestamp"] for c in fake_cdx.calls] == [None, None]
    assert [c["end_timestamp"] for c in fake_cdx.calls] == ["20261106", "20261106"]


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
        log_path=str(log_path),
    )
    events = _events(log_path)
    lookups = [e for e in events if e["event"] == "lookup"]
    assert [e["outcome"] for e in lookups] == ["success", "error"]
    entry = events[-1]
    assert entry["event"] == "batch"
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


@pytest.mark.parametrize("retry_after", ["86400", "Fri, 01 Jan 9999 00:00:00 GMT"])
def test_long_retry_after_is_capped(clock, flaky, retry_after) -> None:
    flaky.errors = [_http_error(429, retry_after=retry_after)]
    wayback.get_url_records("example.com", min_interval=0)
    assert clock.sleeps == [wb.MAX_RETRY_AFTER]


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


def test_retry_events(clock, flaky, caplog) -> None:
    flaky.errors = [requests.ConnectionError("[Errno 111] Connection refused")]
    events: list[dict[str, object]] = []
    wayback.get_url_records("example.com", min_interval=0, on_event=events.append)
    assert _kinds(events) == ["request", "retry", "request", "lookup"]
    first, retry, second, lookup = events
    assert first["outcome"] == "error" and first["attempt"] == 1
    assert first["error_type"] == "ConnectionError"
    assert retry["attempt"] == 1 and retry["max_retries"] == wb.MAX_RETRIES
    assert retry["delay_s"] == wb.BACKOFF_SECONDS
    assert retry["retry_after"] is None
    assert retry["error"] == "[Errno 111] Connection refused"
    assert second["outcome"] == "ok" and second["attempt"] == 2
    assert lookup["attempts"] == 2
    assert lookup["total_waited_s"] == wb.BACKOFF_SECONDS
    # The warning says the same as the event
    assert "retry 1/3 for example.com in 5.0s" in caplog.text


def test_retry_event_carries_retry_after(clock, flaky) -> None:
    flaky.errors = [_http_error(429, retry_after="42")]
    events: list[dict[str, object]] = []
    wayback.get_url_records("example.com", min_interval=0, on_event=events.append)
    retry = events[1]
    assert retry["retry_after"] == 42.0 and retry["delay_s"] == 42.0
    assert retry["error_type"] == "HTTPError"


def test_final_failure_events(clock, flaky) -> None:
    flaky.errors = [requests.ConnectionError("refused") for _ in range(3)]
    events: list[dict[str, object]] = []
    with pytest.raises(requests.ConnectionError):
        wayback.get_url_records(
            "example.com", min_interval=0, max_retries=2, on_event=events.append
        )
    assert _kinds(events) == [
        "request",
        "retry",
        "request",
        "retry",
        "request",
        "lookup",
    ]
    lookup = events[-1]
    assert lookup["outcome"] == "error" and lookup["attempts"] == 3
    assert lookup["error"] == "refused"
    assert lookup["total_waited_s"] == wb.BACKOFF_SECONDS * 3


# Review follow-up


def test_empty_log_path_counts_as_none(tmp_path, monkeypatch, fake_cdx) -> None:
    monkeypatch.chdir(tmp_path)
    wayback.get_url_records("example.com", log_path="")
    assert list(tmp_path.iterdir()) == []
    with pytest.warns(DeprecationWarning):
        wayback.get_url_snapshots(
            "example.com", verbose=False, save_log=True, log_path=""
        )
    assert _kinds(_events(tmp_path / wb.LOG_PATH)) == ["request", "lookup"]


def test_raising_handler_is_not_called_again(clock, flaky) -> None:
    flaky.errors = [ValueError("bad filter")]
    calls: list[object] = []

    def handler(event: dict[str, object]) -> None:
        calls.append(event["event"])
        raise OSError("disk full")

    with pytest.raises(OSError, match="disk full") as info:
        wayback.get_url_records("example.com", min_interval=0, on_event=handler)
    assert calls == ["request"]
    # The lookup's own error is kept as the context of the handler's
    assert isinstance(info.value.__context__, ValueError)


def test_raising_handler_on_success_is_not_called_again(flaky) -> None:
    calls: list[object] = []

    def handler(event: dict[str, object]) -> None:
        calls.append(event["event"])
        raise OSError("disk full")

    with pytest.raises(OSError):
        wayback.get_url_records("example.com", min_interval=0, on_event=handler)
    assert calls == ["request"]


def test_handler_edits_do_not_reach_the_lookup(clock, flaky) -> None:
    flaky.errors = [requests.ConnectionError("refused")]
    collapses = ["digest"]

    def handler(event: dict[str, Any]) -> None:
        if event["event"] == "request":
            event["filters"].append("statuscode:404")
            event["collapses"].clear()

    wayback.get_url_records(
        "example.com",
        status=200,
        collapses=collapses,
        min_interval=0,
        on_event=handler,
    )
    assert [c["filters"] for c in flaky.calls] == [["statuscode:200"]] * 2
    assert [c["collapses"] for c in flaky.calls] == [["digest"]] * 2
    assert collapses == ["digest"]


def test_writers_share_one_lock(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    big = "x" * 100_000  # far past a write buffer, so a line takes several writes

    def write(n: int) -> None:
        log = wayback.JsonlEventLog(path)  # a writer per thread, as each lookup has
        for i in range(10):
            log({"event": "request", "thread": n, "i": i, "pad": big})

    threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    events = _events(path)
    assert len(events) == 80
    assert {(e["thread"], e["i"]) for e in events} == {
        (n, i) for n in range(8) for i in range(10)
    }


def test_batch_event_params(tmp_path, fake_cdx) -> None:
    events: list[dict[str, Any]] = []
    wayback.get_url_list_snapshots(
        ["a.example"],
        "20200101",
        "20201231",
        output_directory=str(tmp_path / "out"),
        sleep_time=0,
        status=200,
        collapses=["digest"],
        on_event=events.append,
    )
    lookup, batch = events[-2], events[-1]
    keys = ("start_timestamp", "end_timestamp", "filters", "collapses")
    assert {k: batch[k] for k in keys} == {k: lookup[k] for k in keys}
