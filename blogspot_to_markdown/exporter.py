from __future__ import annotations

import hashlib
import logging
import mimetypes
import re
import unicodedata
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, unquote, urlparse

import requests
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning
from markdownify import markdownify as md
import yaml

BLOGGER_API_BASE_URL = "https://www.googleapis.com/blogger/v3"
REQUEST_TIMEOUT_SECONDS = 30
MAX_RESULTS_PER_PAGE = 500
MAX_FILENAME_BYTES = 240
MAX_ASSET_NAME_BYTES = 80
ASSET_DIR_NAME = "_assets"
ASSET_MANIFEST_NAME = "manifest.yml"
ASSET_HASH_CHARS = 12
UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WHITESPACE = re.compile(r"\s+")
REPEATED_UNDERSCORES = re.compile(r"_+")
MARKDOWN_LINKED_IMAGE = re.compile(r"\[!\[([^\]]*)\]\(([^)\s]+)\)\]\(([^)\s]+)\)")
MARKDOWN_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
IMAGE_EXTENSION_ALIASES = {
    "apng": "apng",
    "avif": "avif",
    "bmp": "bmp",
    "gif": "gif",
    "jpeg": "jpg",
    "jpg": "jpg",
    "png": "png",
    "svg": "svg",
    "webp": "webp",
}
CONTENT_TYPE_EXTENSIONS = {
    "image/apng": "apng",
    "image/avif": "avif",
    "image/bmp": "bmp",
    "image/gif": "gif",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/svg+xml": "svg",
    "image/webp": "webp",
}

logger = logging.getLogger(__name__)


class BloggerExportError(RuntimeError):
    """Raised when Blogger export cannot continue safely."""


SaveStatus = Literal["written", "skipped", "updated", "conflict"]
AssetStatus = Literal["downloaded", "reused", "failed"]


@dataclass(frozen=True)
class MarkdownSaveResult:
    path: Path
    status: SaveStatus


@dataclass(frozen=True)
class AssetResult:
    original_url: str
    local_path: str | None
    status: AssetStatus
    content_type: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    error: str | None = None


@dataclass
class AssetArchiveSummary:
    scanned: int = 0
    rewritten: int = 0
    skipped: int = 0
    conflicts: int = 0
    downloaded: int = 0
    reused: int = 0
    failed: int = 0


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


def _unique_conflict_path(path: Path) -> Path:
    counter = 1
    while True:
        suffix = "_conflict" if counter == 1 else f"_conflict_{counter}"
        candidate = path.with_name(f"{path.stem}{suffix}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _is_remote_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _normalized_content_type(value: str | None) -> str | None:
    if not value:
        return None
    content_type = value.split(";", 1)[0].strip().lower()
    return content_type or None


def _extension_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    suffix = Path(unquote(parsed.path)).suffix.lower().lstrip(".")
    if suffix in IMAGE_EXTENSION_ALIASES:
        return IMAGE_EXTENSION_ALIASES[suffix]

    query = parse_qs(parsed.query)
    for key in ("format", "fm", "ext"):
        for value in query.get(key, []):
            normalized = value.lower().lstrip(".")
            if normalized in IMAGE_EXTENSION_ALIASES:
                return IMAGE_EXTENSION_ALIASES[normalized]
    return None


def _extension_from_content_type(content_type: str | None) -> str | None:
    if content_type in CONTENT_TYPE_EXTENSIONS:
        return CONTENT_TYPE_EXTENSIONS[content_type]
    if content_type:
        guessed = mimetypes.guess_extension(content_type)
        if guessed:
            normalized = guessed.lower().lstrip(".")
            if normalized == "jpe":
                return "jpg"
            return normalized
    return None


def _asset_extension(url: str, content_type: str | None = None) -> str:
    return (
        _extension_from_url(url)
        or _extension_from_content_type(content_type)
        or "bin"
    )


def _is_image_content_type(content_type: str | None) -> bool:
    return bool(content_type and content_type.startswith("image/"))


def _sniff_image_content_type(content: bytes) -> str | None:
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    stripped = content.lstrip()[:256].lower()
    if stripped.startswith(b"<svg") or b"<svg" in stripped[:64]:
        return "image/svg+xml"
    return None


def _extract_html_image_source(content: bytes) -> str | None:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)
        soup = BeautifulSoup(content.decode("utf-8", errors="replace"), "html.parser")

    image = soup.find("img")
    if image is None:
        return None
    src = image.get("src")
    if isinstance(src, str) and _is_remote_url(src):
        return src
    return None


def _asset_name_from_url(url: str) -> str:
    parsed = urlparse(url)
    name = unquote(Path(parsed.path).name)
    suffix = Path(name).suffix
    stem = name[: -len(suffix)] if suffix else name
    if not stem:
        stem = parsed.netloc or "asset"
    return _truncate_utf8(_safe_filename_part(stem), MAX_ASSET_NAME_BYTES)


def _asset_post_key(post_key: str) -> str:
    return _truncate_utf8(_safe_filename_part(post_key), MAX_ASSET_NAME_BYTES)


def _build_asset_filename(
    url: str,
    occurrence_index: int,
    content_type: str | None = None,
) -> str:
    url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:ASSET_HASH_CHARS]
    name = _asset_name_from_url(url)
    extension = _asset_extension(url, content_type)
    return f"{occurrence_index:03d}_{url_hash}_{name}.{extension}"


def _looks_like_image_resource_url(url: str) -> bool:
    if not _is_remote_url(url):
        return False

    if _extension_from_url(url):
        return True

    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    return (
        host.endswith("blogger.googleusercontent.com")
        or host.endswith("bp.blogspot.com")
        or (host == "docs.google.com" and path.endswith("/image"))
        or (host == "pbs.twimg.com" and path.startswith("/media/"))
    )


def _path_to_manifest_entry(result: AssetResult) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "original_url": result.original_url,
        "status": result.status,
        "local_path": result.local_path,
        "content_type": result.content_type,
        "size_bytes": result.size_bytes,
        "sha256": result.sha256,
        "error": result.error,
    }
    return {key: value for key, value in entry.items() if value is not None}


def _asset_download_error_message(
    exc: requests.RequestException,
    response: requests.Response | None,
) -> str:
    if isinstance(exc, requests.HTTPError):
        response_obj = getattr(exc, "response", None) or response
        status_code = getattr(response_obj, "status_code", None)
        if status_code:
            return f"Image download failed with HTTP {status_code}."
    return "Image download failed before receiving a usable response."


class AssetArchiver:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.assets_dir = self.directory / ASSET_DIR_NAME
        self.manifest_path = self.assets_dir / ASSET_MANIFEST_NAME
        self.entries = self._load_manifest()
        self.downloaded = 0
        self.reused = 0
        self.failed = 0
        self._changed = False

    def _load_manifest(self) -> dict[str, dict[str, Any]]:
        try:
            raw_text = self.manifest_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}

        try:
            payload = yaml.safe_load(raw_text) or {}
        except yaml.YAMLError:
            logger.warning("Ignoring invalid asset manifest: %s", self.manifest_path)
            return {}

        if not isinstance(payload, dict):
            return {}

        entries: dict[str, dict[str, Any]] = {}
        raw_assets = payload.get("assets", [])
        if not isinstance(raw_assets, list):
            return entries

        for raw_entry in raw_assets:
            if not isinstance(raw_entry, dict):
                continue
            original_url = raw_entry.get("original_url")
            if not isinstance(original_url, str) or not original_url:
                continue
            entries[original_url] = dict(raw_entry)
        return entries

    def _existing_success(self, url: str) -> AssetResult | None:
        entry = self.entries.get(url)
        if not entry or entry.get("status") not in {"downloaded", "reused"}:
            return None

        content_type = _normalized_content_type(str(entry.get("content_type") or ""))
        if content_type and not _is_image_content_type(content_type):
            return None

        local_path = entry.get("local_path")
        if not isinstance(local_path, str) or not local_path:
            return None

        asset_path = self.directory / local_path
        if not asset_path.exists():
            return None

        self.reused += 1
        return AssetResult(
            original_url=url,
            local_path=local_path,
            status="reused",
            content_type=str(entry.get("content_type") or "") or None,
            size_bytes=entry.get("size_bytes")
            if isinstance(entry.get("size_bytes"), int)
            else None,
            sha256=str(entry.get("sha256") or "") or None,
        )

    def _record_failure(self, url: str, message: str) -> AssetResult:
        logger.warning("%s URL: %s", message, url)
        result = AssetResult(
            original_url=url,
            local_path=None,
            status="failed",
            error=message,
        )
        self.entries[url] = _path_to_manifest_entry(result)
        self.failed += 1
        self._changed = True
        return result

    def archive(
        self,
        url: str,
        *,
        post_key: str,
        occurrence_index: int,
    ) -> AssetResult:
        existing = self._existing_success(url)
        if existing is not None:
            return existing

        response: requests.Response | None = None
        try:
            response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
        except requests.RequestException as exc:
            return self._record_failure(
                url,
                _asset_download_error_message(exc, response),
            )

        content = response.content
        content_type = _normalized_content_type(response.headers.get("Content-Type"))
        sniffed_type = _sniff_image_content_type(content)
        if not _is_image_content_type(content_type) and sniffed_type:
            content_type = sniffed_type

        if not _is_image_content_type(content_type):
            wrapped_image_url = None
            if content_type == "text/html":
                wrapped_image_url = _extract_html_image_source(content)

            if wrapped_image_url:
                nested_response: requests.Response | None = None
                try:
                    nested_response = requests.get(
                        wrapped_image_url,
                        timeout=REQUEST_TIMEOUT_SECONDS,
                    )
                    nested_response.raise_for_status()
                except requests.RequestException as exc:
                    return self._record_failure(
                        url,
                        _asset_download_error_message(exc, nested_response),
                    )

                content = nested_response.content
                content_type = _normalized_content_type(
                    nested_response.headers.get("Content-Type")
                )
                sniffed_type = _sniff_image_content_type(content)
                if not _is_image_content_type(content_type) and sniffed_type:
                    content_type = sniffed_type

            if not _is_image_content_type(content_type):
                return self._record_failure(
                    url,
                    "Image download returned non-image content.",
                )

        relative_path = (
            Path(ASSET_DIR_NAME)
            / _asset_post_key(post_key)
            / _build_asset_filename(url, occurrence_index, content_type)
        )
        asset_path = self.directory / relative_path
        local_path = relative_path.as_posix()
        existing_entry = self.entries.get(url)
        existing_content_type = None
        if existing_entry:
            existing_content_type = _normalized_content_type(
                str(existing_entry.get("content_type") or "")
            )
        replace_existing = bool(
            existing_content_type
            and not _is_image_content_type(existing_content_type)
        )

        if asset_path.exists() and not replace_existing:
            status: AssetStatus = "reused"
            self.reused += 1
            content = asset_path.read_bytes()
        else:
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            asset_path.write_bytes(content)
            status = "downloaded"
            self.downloaded += 1

        result = AssetResult(
            original_url=url,
            local_path=local_path,
            status=status,
            content_type=content_type,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )
        self.entries[url] = _path_to_manifest_entry(result)
        self._changed = True
        return result

    def write_manifest(self) -> None:
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "assets": [
                self.entries[url]
                for url in sorted(self.entries)
            ],
        }
        self.manifest_path.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        self._changed = False


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


def _post_asset_key(post: dict[str, Any]) -> str:
    blogger_id = _post_blogger_id(post)
    if blogger_id:
        return blogger_id
    return Path(_build_filename(_post_publish_date(post), _post_title(post))).stem


def _archive_image_url(
    asset_archiver: AssetArchiver,
    url: str,
    *,
    post_key: str,
    occurrence_index: int,
) -> str | None:
    if not _is_remote_url(url):
        return None
    result = asset_archiver.archive(
        url,
        post_key=post_key,
        occurrence_index=occurrence_index,
    )
    return result.local_path


def _localize_html_assets(
    html: str,
    asset_archiver: AssetArchiver,
    *,
    post_key: str,
) -> str:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)
        soup = BeautifulSoup(html, "html.parser")
    occurrence_index = 0

    for image in soup.find_all("img"):
        src = image.get("src")
        if not isinstance(src, str) or not _is_remote_url(src):
            continue

        occurrence_index += 1
        parent = image.parent
        href = None
        if getattr(parent, "name", None) == "a":
            raw_href = parent.get("href")
            if isinstance(raw_href, str) and _looks_like_image_resource_url(raw_href):
                href = raw_href

        asset_url = href or src
        local_path = _archive_image_url(
            asset_archiver,
            asset_url,
            post_key=post_key,
            occurrence_index=occurrence_index,
        )
        if not local_path:
            continue

        image["src"] = local_path
        if href is not None:
            parent["href"] = local_path

    return str(soup)


def _markdown_content(
    post: dict[str, Any],
    *,
    asset_archiver: AssetArchiver | None = None,
) -> str:
    content = _post_text(post, "content")
    if asset_archiver is not None:
        content = _localize_html_assets(
            content,
            asset_archiver,
            post_key=_post_asset_key(post),
        )
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)
        return md(content).strip()


def _render_markdown(
    post: dict[str, Any],
    *,
    asset_archiver: AssetArchiver | None = None,
) -> str:
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
    content_md = _markdown_content(post, asset_archiver=asset_archiver)
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


def _rewrite_existing_markdown_assets(
    text: str,
    asset_archiver: AssetArchiver,
    *,
    post_key: str,
) -> str:
    occurrence_index = 0

    def archive_markdown_url(url: str) -> str:
        nonlocal occurrence_index
        if not _is_remote_url(url):
            return url
        occurrence_index += 1
        return (
            _archive_image_url(
                asset_archiver,
                url,
                post_key=post_key,
                occurrence_index=occurrence_index,
            )
            or url
        )

    def replace_linked_image(match: re.Match[str]) -> str:
        alt_text = match.group(1)
        image_url = match.group(2)
        link_url = match.group(3)

        if _looks_like_image_resource_url(link_url):
            local_path = archive_markdown_url(link_url)
            if local_path != link_url:
                return f"[![{alt_text}]({local_path})]({local_path})"

        local_image_url = archive_markdown_url(image_url)
        if local_image_url != image_url:
            return f"[![{alt_text}]({local_image_url})]({link_url})"
        return match.group(0)

    def replace_image(match: re.Match[str]) -> str:
        alt_text = match.group(1)
        image_url = match.group(2)
        local_image_url = archive_markdown_url(image_url)
        if local_image_url != image_url:
            return f"![{alt_text}]({local_image_url})"
        return match.group(0)

    text = MARKDOWN_LINKED_IMAGE.sub(replace_linked_image, text)
    return MARKDOWN_IMAGE.sub(replace_image, text)


def _markdown_post_key(path: Path, text: str) -> str:
    metadata = _extract_front_matter(text)
    if metadata is not None:
        blogger_id = metadata.get("blogger_id")
        if blogger_id:
            return str(blogger_id)
    return path.stem


def archive_existing_markdown_assets(
    directory: str | Path,
    *,
    overwrite: bool = False,
) -> AssetArchiveSummary:
    output_path = Path(directory)
    output_path.mkdir(parents=True, exist_ok=True)
    asset_archiver = AssetArchiver(output_path)
    summary = AssetArchiveSummary()

    for path in sorted(output_path.glob("*.md")):
        if path.name == "README.md":
            continue

        summary.scanned += 1
        original_text = path.read_text(encoding="utf-8")
        rewritten_text = _rewrite_existing_markdown_assets(
            original_text,
            asset_archiver,
            post_key=_markdown_post_key(path, original_text),
        )
        if rewritten_text == original_text:
            summary.skipped += 1
            continue

        if overwrite:
            _write_markdown(path, rewritten_text)
            summary.rewritten += 1
            logger.info("Localized image assets in existing Markdown: %s", path)
            continue

        conflict_path = _unique_conflict_path(path)
        _write_markdown(conflict_path, rewritten_text)
        summary.conflicts += 1
        logger.warning(
            "Wrote conflict copy with localized image assets: %s",
            conflict_path,
        )

    asset_archiver.write_manifest()
    summary.downloaded = asset_archiver.downloaded
    summary.reused = asset_archiver.reused
    summary.failed = asset_archiver.failed
    logger.info(
        "Asset archive scan complete: scanned=%s, rewritten=%s, skipped=%s, "
        "conflicts=%s, downloaded=%s, reused=%s, failed=%s, manifest='%s'.",
        summary.scanned,
        summary.rewritten,
        summary.skipped,
        summary.conflicts,
        summary.downloaded,
        summary.reused,
        summary.failed,
        asset_archiver.manifest_path,
    )
    return summary


def _save_markdown_result(
    post: dict[str, Any],
    directory: str | Path,
    *,
    overwrite: bool = False,
    archive_assets: bool = False,
    asset_archiver: AssetArchiver | None = None,
) -> MarkdownSaveResult:
    title = _post_title(post)
    publish_date = _post_publish_date(post)
    output_path = Path(directory)
    output_path.mkdir(parents=True, exist_ok=True)
    owns_asset_archiver = archive_assets and asset_archiver is None
    if owns_asset_archiver:
        asset_archiver = AssetArchiver(output_path)

    try:
        rendered = _render_markdown(
            post,
            asset_archiver=asset_archiver if archive_assets else None,
        )

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
    finally:
        if owns_asset_archiver and asset_archiver is not None:
            asset_archiver.write_manifest()


def save_markdown(
    post: dict[str, Any],
    directory: str | Path,
    *,
    overwrite: bool = False,
    archive_assets: bool = False,
) -> Path:
    return _save_markdown_result(
        post,
        directory,
        overwrite=overwrite,
        archive_assets=archive_assets,
    ).path


def export_blog(
    blog_url: str,
    api_key: str,
    output_dir: str | Path,
    *,
    overwrite: bool = False,
    archive_assets: bool = False,
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
    asset_archiver = AssetArchiver(output_path) if archive_assets else None
    for index, post in enumerate(posts, start=1):
        title = _post_title(post)
        logger.info("Exporting post %s/%s: %s", index, len(posts), title)
        result = _save_markdown_result(
            post,
            output_path,
            overwrite=overwrite,
            archive_assets=archive_assets,
            asset_archiver=asset_archiver,
        )
        counts[result.status] += 1
        if result.status == "skipped":
            logger.info("Skipped unchanged post: %s", title)
        elif result.status == "conflict":
            logger.warning("Wrote conflict copy for changed post: %s", result.path)
        elif result.status == "updated":
            logger.info("Updated exported post: %s", result.path)
        else:
            logger.info("Exported post: %s", result.path)

    if asset_archiver is not None:
        asset_archiver.write_manifest()
        logger.info(
            "Asset archive complete for fetched posts: downloaded=%s, reused=%s, "
            "failed=%s, manifest='%s'.",
            asset_archiver.downloaded,
            asset_archiver.reused,
            asset_archiver.failed,
            asset_archiver.manifest_path,
        )
        archive_existing_markdown_assets(output_path, overwrite=overwrite)

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
