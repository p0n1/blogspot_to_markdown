from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path
from typing import Any

import requests
from markdownify import markdownify as md

BLOGGER_API_BASE_URL = "https://www.googleapis.com/blogger/v3"
REQUEST_TIMEOUT_SECONDS = 30
MAX_RESULTS_PER_PAGE = 500
MAX_FILENAME_BYTES = 240
UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WHITESPACE = re.compile(r"\s+")
REPEATED_UNDERSCORES = re.compile(r"_+")

logger = logging.getLogger(__name__)


def fetch_posts(blog_url: str, api_key: str) -> list[dict[str, Any]]:
    """Fetch all posts for a public Blogspot blog."""
    blog_id_response = requests.get(
        f"{BLOGGER_API_BASE_URL}/blogs/byurl",
        params={"key": api_key, "url": blog_url},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    blog_id_response.raise_for_status()
    blog_id = blog_id_response.json()["id"]

    posts: list[dict[str, Any]] = []
    page_token: str | None = None

    while True:
        params: dict[str, str | int] = {
            "key": api_key,
            "maxResults": MAX_RESULTS_PER_PAGE,
        }
        if page_token:
            params["pageToken"] = page_token

        posts_response = requests.get(
            f"{BLOGGER_API_BASE_URL}/blogs/{blog_id}/posts",
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        posts_response.raise_for_status()

        payload = posts_response.json()
        posts.extend(payload.get("items", []))
        page_token = payload.get("nextPageToken")
        if not page_token:
            return posts


def _safe_filename_part(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    normalized = WHITESPACE.sub("_", normalized.strip())
    normalized = UNSAFE_FILENAME_CHARS.sub("_", normalized)
    normalized = REPEATED_UNDERSCORES.sub("_", normalized)
    normalized = normalized.strip("._ ")
    return normalized or "untitled"


def _truncate_utf8(value: str, max_bytes: int) -> str:
    max_bytes = max(1, max_bytes)
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value

    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    truncated = truncated.rstrip("._ ")
    return truncated or "untitled"


def _build_filename(publish_date: str, title: str, suffix: str = "") -> str:
    prefix = f"{publish_date}_"
    extension = ".md"
    available_title_bytes = MAX_FILENAME_BYTES - len(
        f"{prefix}{suffix}{extension}".encode("utf-8")
    )
    safe_title = _safe_filename_part(title)
    safe_title = _truncate_utf8(safe_title, available_title_bytes)
    return f"{prefix}{safe_title}{suffix}{extension}"


def _unique_markdown_path(directory: Path, publish_date: str, title: str) -> Path:
    counter = 1
    while True:
        suffix = "" if counter == 1 else f"_{counter}"
        candidate = directory / _build_filename(publish_date, title, suffix=suffix)
        if not candidate.exists():
            return candidate
        counter += 1


def save_markdown(post: dict[str, Any], directory: str | Path) -> Path:
    title = post["title"]
    content_md = md(post["content"]).strip()
    publish_date = post["published"][:10]
    original_url = post["url"]
    output_path = Path(directory)
    output_path.mkdir(parents=True, exist_ok=True)
    filepath = _unique_markdown_path(output_path, publish_date, title)

    parts = [f"# {title}", f"> Original URL: {original_url}"]
    if content_md:
        parts.append(content_md)

    filepath.write_text(
        "\n\n".join(parts) + "\n",
        encoding="utf-8",
    )
    return filepath


def export_blog(blog_url: str, api_key: str, output_dir: str | Path) -> int:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    logger.info("Starting the export process.")
    posts = fetch_posts(blog_url, api_key)
    logger.info("Fetched %s posts.", len(posts))

    for index, post in enumerate(posts, start=1):
        logger.info("Exporting post %s/%s: %s", index, len(posts), post["title"])
        save_markdown(post, output_path)
        logger.info("Successfully exported: %s", post["title"])

    logger.info("Exported %s posts to the '%s' directory.", len(posts), output_path)
    return len(posts)
