from browser.browser_manager import BrowserManager
from browser._md_convert import MarkdownConverter
from utils.files import ensure_claude_workspace, get_file_
from utils.helpers import tokenizer
from urllib.parse import urlparse
import mimetypes
import requests
import re
import os

browser_manager = BrowserManager()

##############################################################################################################


def web_search(query: str, filter_year: int, *, claude_id: str) -> str:
    """search the web for information
    #parameters:
    query: a text query to search for in the web
    filter_year: an optional year filter (e.g., 2020)
    """
    max_tokens = 60000
    browser = browser_manager.get_browser(claude_id)
    browser.visit_page(f"google: {query}", filter_year=None)
    header, content = browser._state()
    result = header.strip() + "\n=======================\n" + content
    pattern = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
    sources = []
    for match in pattern.finditer(content):
        url = match.group(1)
        sources.append(
            {
                "url": url,
                "source": url,
                "page": None,
                "image_path": None,
            }
        )
    return result, result, sources, max_tokens


def visit_url(url: str, *, claude_id: str) -> str:
    """Visit a webpage at a given URL and return its text. Given a url to a YouTube video, this returns the transcript. if you give this file url like "https://example.com/file.pdf", it will download that file and then you can use text_file tool on it.
    #parameters:
    url: the relative or absolute url of the webapge to visit
    """
    max_tokens = 60000
    browser = browser_manager.get_browser(claude_id)
    browser.visit_page(url)
    sources = []
    header, content = browser._state()
    sources.append(
        {
            "url": url,
            "source": url,
            "page": None,
            "image_path": None,
        }
    )
    result = header.strip() + "\n=======================\n" + content
    return result, result, sources, max_tokens


def download_from_url(url: str, *, claude_id: str) -> str:
    """Download a file at a given URL. The file should be of this format: [".xlsx", ".pptx", ".wav", ".mp3", ".png", ".docx"]. DO NOT use this tool for .pdf or .txt or .htm files: for these types of files use visit_url with the file url instead.
    #parameters:
    url: the relative or absolute url of the file to be downloaded
    """
    max_tokens = 60000
    if "arxiv" in url:
        url = url.replace("abs", "pdf")
    response = requests.get(url)

    # Try to get extension from URL first
    url_path = urlparse(url).path
    extension = os.path.splitext(url_path)[1]

    # If no extension in URL, try from content-type
    if not extension:
        content_type = response.headers.get("content-type", "").split(";")[0].strip()
        extension = mimetypes.guess_extension(content_type)

    claude_folder = ensure_claude_workspace(claude_id)
    if extension and isinstance(extension, str):
        new_path = f"{claude_folder}/file{extension}"
    else:
        new_path = f"{claude_folder}/file.object"
    with open(new_path, "wb") as f:
        f.write(response.content)
    if "pdf" in extension or "txt" in extension or "htm" in extension:
        return (
            "Do not use this tool for pdf or txt or html files: use visit_page instead.",
            "",
            [],
            max_tokens,
        )
    result = f"file was downloaded and saved. You can now review it by calling text_file with the filename."
    return result, result, [], max_tokens


def archive_search(url: str, date: str, *, claude_id: str) -> str:
    """Given a url, searches the Wayback Machine and returns the archived version of the url that's closest in time to the desired date.
    #parameters:
    url: The url to archive.
    date: The desired date in 'YYYYMMDD' format.
    """
    max_tokens = 60000
    browser = browser_manager.get_browser(claude_id)
    base_api = f"https://archive.org/wayback/available?url={url}"
    archive_api = base_api + f"&timestamp={date}"
    res_with_ts = requests.get(archive_api).json()
    res_without_ts = requests.get(base_api).json()
    sources = []
    sources.append(
        {
            "url": url,
            "source": url,
            "page": None,
            "image_path": None,
        }
    )
    if (
        "archived_snapshots" in res_with_ts
        and "closest" in res_with_ts["archived_snapshots"]
    ):
        closest = res_with_ts["archived_snapshots"]["closest"]
    elif (
        "archived_snapshots" in res_without_ts
        and "closest" in res_without_ts["archived_snapshots"]
    ):
        closest = res_without_ts["archived_snapshots"]["closest"]
    else:
        result = f"Archive not found for {url}."
        return (
            result,
            result,
            sources,
            max_tokens,
        )
    target_url = closest["url"]
    browser.visit_page(target_url)
    header, content = browser._state()
    result = (
        f"web archive for url {url}, snapshot on {closest['timestamp'][:8]}:\n"
        + header.strip()
        + "\n=======================\n"
        + content
    )
    return (
        result,
        result,
        sources,
        max_tokens,
    )


def page_up(claude_id: str) -> str:
    """Scroll up one page."""
    max_tokens = 60000
    browser = browser_manager.get_browser(claude_id)
    browser.page_up()
    header, content = browser._state()
    result = header.strip() + "\n=======================\n" + content
    return result, result, [], max_tokens


def page_down(claude_id: str) -> str:
    """Scroll down one page."""
    max_tokens = 60000
    browser = browser_manager.get_browser(claude_id)
    browser.page_down()
    header, content = browser._state()
    result = header.strip() + "\n=======================\n" + content
    return result, result, [], max_tokens


def find_on_page(search_string: str, *, claude_id: str) -> str:
    """Scroll the viewport to the first occurrence of the search string. This is equivalent to Ctrl+F.
    #parameters:
    search_string: The string to search for; supports wildcards like '*'
    """
    max_tokens = 60000
    browser = browser_manager.get_browser(claude_id)
    result = browser.find_on_page(search_string)
    header, content = browser._state()
    if result is None:
        return (
            (
                header.strip()
                + f"\n=======================\nThe search string '{search_string}' was not found on this page."
            ),
            "",
            [],
            max_tokens,
        )
    end_result = header.strip() + "\n=======================\n" + content
    return end_result, end_result, [], max_tokens


def find_next(claude_id: str) -> str:
    """Find next ocurrence."""
    max_tokens = 60000
    browser = browser_manager.get_browser(claude_id)
    result = browser.find_next()
    header, content = browser._state()
    if result is None:
        return (
            (
                header.strip()
                + "\n=======================\nNo further occurrences found."
            ),
            "",
            [],
            max_tokens,
        )
    end_result = header.strip() + "\n=======================\n" + content
    return (
        end_result,
        end_result,
        [],
        max_tokens,
    )


def text_file(file: str, *, claude_id: str) -> str:
    """use this tool on files you download. this tool converts the following files to markdown for you to review. it can convert these file extensions [".html", ".htm", ".xlsx", ".pptx", ".wav", ".mp3", ".flac", ".pdf", ".docx"], and all other types of text files. IT DOES NOT HANDLE IMAGES.
    #parameters:
    file: filename which u downloaded
    """
    md_converter = MarkdownConverter()
    max_tokens = 60000
    try:
        filename = os.path.basename(file)
        file_path = get_file_(claude_id, filename)
        if file_path is None:
            return f"File not found: {filename}", "", "", 5000
        _, ext = os.path.splitext(file_path)
        ext = ext.lower()
        if ext in [".webp", ".png", ".jpeg", ".jpg"]:
            return "Use vision instead for image files.", "", "", 5000
        result = md_converter.convert_local(file_path)
        text = result.text_content
        text_tokens = tokenizer.encode(text)
        current_token_count = len(text_tokens)
        if current_token_count > max_tokens:
            text_tokens = text_tokens[:max_tokens]
            trimmed_text = tokenizer.decode(text_tokens)
        else:
            trimmed_text = text
        return (
            "document content: " + trimmed_text,
            "document content: " + trimmed_text,
            [],
            max_tokens,
        )
    except Exception as e:
        return f"{e}", "", [], max_tokens
