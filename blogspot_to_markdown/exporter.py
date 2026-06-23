from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import requests
from markdownify import markdownify as md

BLOGGER_API_BASE_URL = "https://www.googleapis.com/blogger/v3"
REQUEST_TIMEOUT_SECONDS = 30

logger = logging.getLogger(__name__)


def fetch_posts(blog_url: str, api_key: str) -> list[dict[str, Any]]:
    """Fetch up to 500 posts for a Blogspot blog."""
    blog_id_response = requests.get(
        f"{BLOGGER_API_BASE_URL}/blogs/byurl",
        params={"key": api_key, "url": blog_url},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    blog_id_response.raise_for_status()
    blog_id = blog_id_response.json()["id"]

    posts_response = requests.get(
        f"{BLOGGER_API_BASE_URL}/blogs/{blog_id}/posts",
        params={"key": api_key, "maxResults": 500},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    posts_response.raise_for_status()
    return posts_response.json().get("items", [])


def save_markdown(post: dict[str, Any], directory: str | Path) -> Path:
    title = post["title"]
    content_md = md(post["content"])
    publish_date = post["published"][:10]
    original_url = post["url"]
    filename = f"{publish_date}_{title.replace(' ', '_').replace('/', '_')}.md"
    filepath = Path(directory) / filename

    filepath.write_text(
        f"# {title}\n\n> Original URL: {original_url}\n\n{content_md}",
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
