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
show_cols = ['url', 'statuscode', 'archive_url', 'timestamp']
print(data[show_cols].head(5).to_markdown())
```

**Output**:
```
processing lazerlab.net
extracting items
reshaping items
snapshots found: 207
``````

| url          |   statuscode | archive_url                                                            |      timestamp |
|:-------------|-------------:|:-----------------------------------------------------------------------|---------------:|
| lazerlab.net |          200 | https://web.archive.org/web/20180129175333/http://lazerlab.net:80/     | 20180129175333 |
| lazerlab.net |          200 | https://web.archive.org/web/20180203075259/http://www.lazerlab.net:80/ | 20180203075259 |
| lazerlab.net |          200 | https://web.archive.org/web/20180301214059/http://lazerlab.net:80/     | 20180301214059 |
| lazerlab.net |          200 | https://web.archive.org/web/20180306094604/http://www.lazerlab.net:80/ | 20180306094604 |
| lazerlab.net |          200 | https://web.archive.org/web/20180314153547/http://lazerlab.net/        | 20180314153547 |


## Dependencies

`waybackpy`: For interacting with the Wayback Machine API.  
`requests`: For making HTTP requests.  
`hashlib`: For generating hash values from URLs.  
`pandas`: For data manipulation and analysis.  
`tqdm`: For providing progress bars during data collection.  


## Acknowledgments
This package is primarily a wrapper for [`waybackpy`](https://akamhy.github.io/waybackpy/), which provides a broader range of tools for using the [Wayback Machine CDX Server API](https://github.com/internetarchive/wayback/tree/master/wayback-cdx-server) provided by the [Internet Archive](https://archive.org/). 
