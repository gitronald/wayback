"""Tests for wayback."""

import hashlib
import json

import wayback


def test_public_api() -> None:
    assert callable(wayback.get_url_snapshots)
    assert callable(wayback.get_url_list_snapshots)


def test_batch_run_log(tmp_path, monkeypatch) -> None:
    # Pre-create the output file so the URL is skipped without any network call
    monkeypatch.chdir(tmp_path)
    url = "example.com"
    output_directory = tmp_path / "snapshots"
    output_directory.mkdir()
    url_hash = hashlib.md5(url.encode()).hexdigest()
    (output_directory / f"{url_hash}.csv").write_text("url\n")

    wayback.get_url_list_snapshots(
        urls=[url],
        start_timestamp="20200101",
        end_timestamp="20201231",
        output_directory=str(output_directory),
        sleep_time=0,
    )

    log_lines = (tmp_path / "data" / "wayback-runs.jsonl").read_text().splitlines()
    assert len(log_lines) == 1
    entry = json.loads(log_lines[0])
    assert entry["function"] == "get_url_list_snapshots"
    assert entry["n_urls"] == 1
    assert entry["n_saved"] == 0
    assert entry["n_skipped"] == 1
    assert entry["n_errors"] == 0
    assert entry["results"][0]["status"] == "skipped"
    assert entry["started_at"] and entry["duration_seconds"] >= 0


def test_batch_save_log_false_writes_nothing(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    url = "example.com"
    output_directory = tmp_path / "snapshots"
    output_directory.mkdir()
    url_hash = hashlib.md5(url.encode()).hexdigest()
    (output_directory / f"{url_hash}.csv").write_text("url\n")

    wayback.get_url_list_snapshots(
        urls=[url],
        start_timestamp="20200101",
        end_timestamp="20201231",
        output_directory=str(output_directory),
        sleep_time=0,
        save_log=False,
    )

    assert not (tmp_path / "data").exists()
