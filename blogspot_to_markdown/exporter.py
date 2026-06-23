from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import requests
from markdownify import markdownify as md
import yaml

BLOGGER_API_BASE_URL = "https://www.googleapis.com/blogger/v3"
REQUEST_TIMEOUT_SECONDS = 30
MAX_RESULTS_PER_PAGE = 500
MAX_FILENAME_BYTES = 240
UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WHITESPACE = re.compile(r"\s+")
REPEATED_UNDERSCORES = re.compile(r"_+")

logger = logging.getLogger(__name__)


class BloggerExportError(RuntimeError):
    """Raised when Blogger export cannot continue safely."""


SaveStatus = Literal["written", "skipped", "updated", "conflict"]


@dataclass(frozen=True)
class MarkdownSaveResult:
    path: Path
    status: SaveStatus


def _request_json(
    url: str,
    *,
    params: dict[str, str | int],
    context: str,
) -> dict[str, Any]:
    try:
        response = requests.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise BloggerExportError(
            f"{context} failed before receiving a response."
        ) from exc

    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        status_code = getattr(response, "status_code", None)
        if status_code:
            message = f"{context} failed with HTTP {status_code}."
        else:
            message = f"{context} failed with an HTTP error."
        raise BloggerExportError(message) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise BloggerExportError(f"{context} returned invalid JSON.") from exc

    if not isinstance(payload, dict):
        raise BloggerExportError(f"{context} returned an unexpected response shape.")

    return payload


def fetch_posts(blog_url: str, api_key: str) -> list[dict[str, Any]]:
    """Fetch all posts for a public Blogspot blog."""
    blog_payload = _request_json(
        f"{BLOGGER_API_BASE_URL}/blogs/byurl",
        params={"key": api_key, "url": blog_url},
        context="Fetching blog metadata",
    )
    blog_id = blog_payload.get("id")
    if not isinstance(blog_id, str) or not blog_id:
        raise BloggerExportError("Blogger API response did not include a blog ID.")

    posts: list[dict[str, Any]] = []
    page_token: str | None = None

    while True:
        params: dict[str, str | int] = {
            "key": api_key,
            "maxResults": MAX_RESULTS_PER_PAGE,
        }
        if page_token:
            params["pageToken"] = page_token

        payload = _request_json(
            f"{BLOGGER_API_BASE_URL}/blogs/{blog_id}/posts",
            params=params,
            context="Fetching blog posts",
        )

        items = payload.get("items", [])
        if items is None:
            items = []
        if not isinstance(items, list) or not all(
            isinstance(item, dict) for item in items
        ):
            raise BloggerExportError("Blogger API returned an invalid posts list.")

        posts.extend(items)
        page_token = payload.get("nextPageToken")
        if page_token is not None and not isinstance(page_token, str):
            raise BloggerExportError("Blogger API returned an invalid page token.")
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


def _unique_markdown_path(
    directory: Path,
    publish_date: str,
    title: str,
    *,
    suffix_prefix: str = "",
) -> Path:
    counter = 1
    while True:
        if suffix_prefix:
            suffix = suffix_prefix if counter == 1 else f"{suffix_prefix}_{counter}"
        else:
            suffix = "" if counter == 1 else f"_{counter}"
        candidate = directory / _build_filename(publish_date, title, suffix=suffix)
        if not candidate.exists():
            return candidate
        counter += 1


def _post_text(post: dict[str, Any], key: str, default: str = "") -> str:
    value = post.get(key, default)
    if value is None:
        return default
    return str(value)


def _post_title(post: dict[str, Any]) -> str:
    return _post_text(post, "title") or "Untitled"


def _post_publish_date(post: dict[str, Any]) -> str:
    published = _post_text(post, "published")
    return published[:10] if len(published) >= 10 else "undated"


def _post_blogger_id(post: dict[str, Any]) -> str:
    return _post_text(post, "id")


def _post_labels(post: dict[str, Any]) -> list[str]:
    labels = post.get("labels", [])
    if labels is None:
        return []
    if isinstance(labels, list):
        return [str(label) for label in labels]
    return [str(labels)]


def _markdown_content(post: dict[str, Any]) -> str:
    return md(_post_text(post, "content")).strip()


def _render_markdown(post: dict[str, Any]) -> str:
    title = _post_title(post)
    metadata = {
        "title": title,
        "date": _post_text(post, "published") or None,
        "updated": _post_text(post, "updated") or None,
        "source_url": _post_text(post, "url") or None,
        "blogger_id": _post_blogger_id(post) or None,
        "labels": _post_labels(post),
    }
    front_matter = yaml.safe_dump(
        metadata,
        allow_unicode=True,
        sort_keys=False,
    ).strip()

    parts = [f"# {title}"]
    content_md = _markdown_content(post)
    if content_md:
        parts.append(content_md)

    return f"---\n{front_matter}\n---\n\n" + "\n\n".join(parts) + "\n"


def _render_legacy_markdown(post: dict[str, Any]) -> str:
    title = _post_title(post)
    content_md = _markdown_content(post)
    original_url = _post_text(post, "url")
    parts = [f"# {title}", f"> Original URL: {original_url}"]
    if content_md:
        parts.append(content_md)

    return "\n\n".join(parts) + "\n"


def _extract_front_matter(text: str) -> dict[str, Any] | None:
    if not text.startswith("---\n"):
        return None

    end_marker = "\n---\n"
    end_index = text.find(end_marker, 4)
    if end_index == -1:
        return None

    try:
        metadata = yaml.safe_load(text[4:end_index]) or {}
    except yaml.YAMLError:
        return None

    if not isinstance(metadata, dict):
        return None
    return metadata


def _read_front_matter(path: Path) -> dict[str, Any] | None:
    return _extract_front_matter(path.read_text(encoding="utf-8"))


def _find_front_matter_matches(directory: Path, blogger_id: str) -> list[Path]:
    if not blogger_id:
        return []

    matches: list[Path] = []
    for path in sorted(directory.glob("*.md")):
        metadata = _read_front_matter(path)
        if metadata is None:
            continue
        if str(metadata.get("blogger_id") or "") == blogger_id:
            matches.append(path)
    return matches


def _write_markdown(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _save_markdown_result(
    post: dict[str, Any],
    directory: str | Path,
    *,
    overwrite: bool = False,
) -> MarkdownSaveResult:
    title = _post_title(post)
    publish_date = _post_publish_date(post)
    output_path = Path(directory)
    output_path.mkdir(parents=True, exist_ok=True)
    rendered = _render_markdown(post)

    matches = _find_front_matter_matches(output_path, _post_blogger_id(post))
    for match in matches:
        if match.read_text(encoding="utf-8") == rendered:
            return MarkdownSaveResult(path=match, status="skipped")

    if matches:
        target = matches[0]
        if overwrite:
            _write_markdown(target, rendered)
            return MarkdownSaveResult(path=target, status="updated")

        conflict_path = _unique_markdown_path(
            output_path,
            publish_date,
            title,
            suffix_prefix="_conflict",
        )
        _write_markdown(conflict_path, rendered)
        return MarkdownSaveResult(path=conflict_path, status="conflict")

    base_path = output_path / _build_filename(publish_date, title)
    if base_path.exists():
        existing = base_path.read_text(encoding="utf-8")
        if existing == rendered:
            return MarkdownSaveResult(path=base_path, status="skipped")

        existing_metadata = _extract_front_matter(existing)
        existing_blogger_id = ""
        if existing_metadata is not None:
            existing_blogger_id = str(existing_metadata.get("blogger_id") or "")

        current_blogger_id = _post_blogger_id(post)
        if (
            existing_blogger_id
            and current_blogger_id
            and existing_blogger_id != current_blogger_id
        ):
            filepath = _unique_markdown_path(output_path, publish_date, title)
            _write_markdown(filepath, rendered)
            return MarkdownSaveResult(path=filepath, status="written")

        legacy_rendered = _render_legacy_markdown(post)
        if existing_metadata is None and existing == legacy_rendered:
            _write_markdown(base_path, rendered)
            return MarkdownSaveResult(path=base_path, status="updated")

        if overwrite:
            _write_markdown(base_path, rendered)
            return MarkdownSaveResult(path=base_path, status="updated")

        conflict_path = _unique_markdown_path(
            output_path,
            publish_date,
            title,
            suffix_prefix="_conflict",
        )
        _write_markdown(conflict_path, rendered)
        return MarkdownSaveResult(path=conflict_path, status="conflict")

    filepath = _unique_markdown_path(output_path, publish_date, title)
    _write_markdown(filepath, rendered)
    return MarkdownSaveResult(path=filepath, status="written")


def save_markdown(
    post: dict[str, Any],
    directory: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    return _save_markdown_result(post, directory, overwrite=overwrite).path


def export_blog(
    blog_url: str,
    api_key: str,
    output_dir: str | Path,
    *,
    overwrite: bool = False,
) -> int:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    logger.info("Starting the export process.")
    posts = fetch_posts(blog_url, api_key)
    logger.info("Fetched %s posts.", len(posts))

    counts: dict[SaveStatus, int] = {
        "written": 0,
        "skipped": 0,
        "updated": 0,
        "conflict": 0,
    }
    for index, post in enumerate(posts, start=1):
        title = _post_title(post)
        logger.info("Exporting post %s/%s: %s", index, len(posts), title)
        result = _save_markdown_result(post, output_path, overwrite=overwrite)
        counts[result.status] += 1
        if result.status == "skipped":
            logger.info("Skipped unchanged post: %s", title)
        elif result.status == "conflict":
            logger.warning("Wrote conflict copy for changed post: %s", result.path)
        elif result.status == "updated":
            logger.info("Updated exported post: %s", result.path)
        else:
            logger.info("Exported post: %s", result.path)

    logger.info(
        "Export complete: fetched=%s, written=%s, updated=%s, skipped=%s, "
        "conflicts=%s, output_dir='%s'.",
        len(posts),
        counts["written"],
        counts["updated"],
        counts["skipped"],
        counts["conflict"],
        output_path,
    )
    return len(posts)
