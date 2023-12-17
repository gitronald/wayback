import os
import time
import tqdm
import hashlib
import pandas as pd
from waybackpy import WaybackMachineCDXServerAPI
DEFAULT_UA = "Mozilla/5.0 (Windows NT 5.1; rv:40.0) Gecko/20100101 Firefox/40.0"

def get_url_snapshots(url:str,
                      start_timestamp:str = "20000101",
                      end_timestamp:str = time.strftime("%Y%m%d", time.localtime()),
                      user_agent:str = DEFAULT_UA,
                      verbose:bool = True) -> pd.DataFrame:
    """
    Get Wayback Machine snapshots for a given URL.

    Args:
        url (str): URL to search.
        start_timestamp (str): Start timestamp for Wayback Machine search.
        end_timestamp (str): End timestamp for Wayback Machine search.
        user_agent (str): User agent to use for the request.
        verbose (bool): Whether to print progress.

    Returns:
        pd.DataFrame: DataFrame containing the snapshots.
    """

    if verbose: print(f"processing {url}")
    cdx = WaybackMachineCDXServerAPI(
        url=url, 
        user_agent=user_agent,   
        start_timestamp=start_timestamp, 
        end_timestamp=end_timestamp
    )
    
    if verbose: print("extracting items")
    items = [vars(item) for item in cdx.snapshots()]

    if verbose: print("reshaping items")
    df = pd.DataFrame(items) if items else pd.DataFrame({}, index=[0])
    df['archive_url'] = None if 'archive_url' not in df else df['archive_url']
    df['retrieved_on'] = pd.Timestamp.now()
    df['retrieved_dates'] = str(start_timestamp) + "-" + str(end_timestamp)
    df['url'] = url
    df = df[['url'] + list(df.drop(columns=['url']).columns)]

    return df


def get_url_list_snapshots(urls:list, 
                           start_timestamp:str, 
                           end_timestamp:str, 
                           output_directory:str = None, 
                           sleep_time:int = 5,
                           verbose:bool = False) -> None:
    """
    Process a list of URLs to collect Wayback snapshots and save to individual files with unique IDs as filenames.

    Args:
        urls (list): List of URLs to process.
        start_timestamp (str): Start timestamp for Wayback Machine search.
        end_timestamp (str): End timestamp for Wayback Machine search.
        output_directory (str): Directory to save the output files.
        sleep_time (int): Number of seconds to sleep between requests.
        verbose (bool): Whether to print progress.
    """
    assert output_directory is not None, "output_directory must be specified"
    if not os.path.exists(output_directory):
        os.makedirs(output_directory)

    for url in tqdm.tqdm(urls):
        
        # Create a hash of the URL for the filename
        url_hash = hashlib.md5(url.encode()).hexdigest()
        output_path = os.path.join(output_directory, url_hash + ".csv")

        # Check if the file already exists
        if os.path.exists(output_path):
            print(f"Data for {url} already exists. Skipping.")
            continue

        try:
            df = get_url_snapshots(url, start_timestamp, end_timestamp, verbose=verbose)
            df.to_csv(output_path, index=False)
            if verbose: print(f"saved to: {output_path}")
        except Exception as e:
            print(f"error on {url}: {e}")
        time.sleep(sleep_time)
