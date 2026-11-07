# Wayback

## Overview

`wayback` is a simple Python package built on `waybackpy` and designed to further simplify the process of collecting historical snapshot data of URLs from the [Internet Archive's Wayback Machine](https://archive.org/). It's particularly useful if you want to save all of the IA's snapshot links (e.g. `https://web.archive.org/web/<timestamp>/https://<url_of_interest>`) for a particular set of URLs that you can then sort/filter/examine without having to search and click around the Wayback Machine's interface.

## Installation

```sh
pip install git+https://github.com/gitronald/wayback
```


## Example Usage

```python
import wayback

data = wayback.get_url_snapshots(
    url="lazerlab.net",
    start_timestamp="20180101",
    end_timestamp="20231231",
)
print(f"snapshots found: {len(data):,}")
show_cols = ["url", "statuscode", "archive_url", "timestamp"]
print(data[show_cols].head(5).to_markdown())
```

**Output**:
```
processing lazerlab.net
extracting items
reshaping items
snapshots found: 220
```

|    | url          |   statuscode | archive_url                                                            |      timestamp |
|---:|:-------------|-------------:|:-----------------------------------------------------------------------|---------------:|
|  0 | lazerlab.net |          200 | https://web.archive.org/web/20180129175333/http://lazerlab.net:80/     | 20180129175333 |
|  1 | lazerlab.net |          200 | https://web.archive.org/web/20180203075259/http://www.lazerlab.net:80/ | 20180203075259 |
|  2 | lazerlab.net |          200 | https://web.archive.org/web/20180301214059/http://lazerlab.net:80/     | 20180301214059 |
|  3 | lazerlab.net |          200 | https://web.archive.org/web/20180306094604/http://www.lazerlab.net:80/ | 20180306094604 |
|  4 | lazerlab.net |          200 | https://web.archive.org/web/20180314153547/http://lazerlab.net/        | 20180314153547 |


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

- **Date range**: `get_url_snapshots` searches from the first capture to today.
  `start_timestamp=None` (the default) sets no lower bound, and
  `end_timestamp=None` means the date of the call. Pass `YYYYMMDD` strings to narrow
  it.
- **No snapshots**: an empty result is a DataFrame with zero rows and the usual
  columns (`wayback.SNAPSHOT_COLUMNS`), so `len(data)` is the snapshot count.
- **User-Agent**: requests identify themselves as `wayback/<version>`. Pass
  `user_agent=` to send another.
- **Run log**: off by default. `save_log=True` appends a JSON record of each run (its
  parameters, timing, and outcome) to `log_path`, or to `data/wayback-runs.jsonl`
  relative to the working directory when no path is given:

```python
wayback.get_url_snapshots("lazerlab.net", save_log=True, log_path="logs/wayback.jsonl")
```


## Dependencies

`waybackpy`: For interacting with the Wayback Machine API.  
`requests`: For making HTTP requests.  
`hashlib`: For generating hash values from URLs.  
`pandas`: For data manipulation and analysis.  
`tqdm`: For providing progress bars during data collection.  


## Acknowledgments
This package is primarily a wrapper for [`waybackpy`](https://akamhy.github.io/waybackpy/), which provides a broader range of tools for using the [Wayback Machine CDX Server API](https://github.com/internetarchive/wayback/tree/master/wayback-cdx-server) provided by the [Internet Archive](https://archive.org/). 
