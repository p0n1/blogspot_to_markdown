from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from blogspot_to_markdown import exporter


class StubResponse:
    def __init__(
        self,
        payload: Any,
        *,
        status_code: int = 200,
        json_error: ValueError | None = None,
        http_error: bool = False,
    ) -> None:
        self.payload = payload
        self.status_code = status_code
        self.json_error = json_error
        self.http_error = http_error

    def raise_for_status(self) -> None:
        if self.http_error:
            raise exporter.requests.HTTPError("request failed")

    def json(self) -> Any:
        if self.json_error:
            raise self.json_error
        return self.payload


def make_post(
    title: str,
    content: str = "<p>Hello <strong>world</strong></p>",
    *,
    post_id: str = "post-123",
    labels: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": post_id,
        "title": title,
        "content": content,
        "published": "2024-01-02T03:04:05Z",
        "updated": "2024-01-03T04:05:06Z",
        "url": "https://example.blogspot.com/2024/01/post.html",
        "labels": labels or [],
    }


def read_markdown(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    front_matter, body = text[4:].split("\n---\n", 1)
    if body.startswith("\n"):
        body = body[1:]
    metadata = yaml.safe_load(front_matter)
    assert isinstance(metadata, dict)
    return metadata, body


def test_fetch_posts_follows_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, Any], int]] = []

    def fake_get(
        url: str,
        params: dict[str, Any],
        timeout: int,
    ) -> StubResponse:
        calls.append((url, params, timeout))
        if url.endswith("/blogs/byurl"):
            return StubResponse({"id": "blog-123"})
        if "pageToken" not in params:
            return StubResponse(
                {
                    "items": [{"title": "first"}],
                    "nextPageToken": "next-page",
                }
            )
        return StubResponse({"items": [{"title": "second"}]})

    monkeypatch.setattr(exporter.requests, "get", fake_get)

    posts = exporter.fetch_posts("https://example.blogspot.com", "key-123")

    assert [post["title"] for post in posts] == ["first", "second"]
    assert len(calls) == 3
    assert calls[1][1] == {"key": "key-123", "maxResults": 500}
    assert calls[2][1] == {
        "key": "key-123",
        "maxResults": 500,
        "pageToken": "next-page",
    }
    assert all(call[2] == exporter.REQUEST_TIMEOUT_SECONDS for call in calls)


def test_fetch_posts_handles_missing_items(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(
        url: str,
        params: dict[str, Any],
        timeout: int,
    ) -> StubResponse:
        if url.endswith("/blogs/byurl"):
            return StubResponse({"id": "blog-123"})
        return StubResponse({})

    monkeypatch.setattr(exporter.requests, "get", fake_get)

    assert exporter.fetch_posts("https://example.blogspot.com", "key-123") == []


def test_fetch_posts_fails_when_blog_id_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get(
        url: str,
        params: dict[str, Any],
        timeout: int,
    ) -> StubResponse:
        return StubResponse({})

    monkeypatch.setattr(exporter.requests, "get", fake_get)

    with pytest.raises(exporter.BloggerExportError, match="blog ID"):
        exporter.fetch_posts("https://example.blogspot.com", "key-123")


def test_fetch_posts_fails_on_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(
        url: str,
        params: dict[str, Any],
        timeout: int,
    ) -> StubResponse:
        return StubResponse({}, json_error=ValueError("not json"))

    monkeypatch.setattr(exporter.requests, "get", fake_get)

    with pytest.raises(exporter.BloggerExportError, match="invalid JSON"):
        exporter.fetch_posts("https://example.blogspot.com", "key-123")


def test_fetch_posts_wraps_http_errors_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get(
        url: str,
        params: dict[str, Any],
        timeout: int,
    ) -> StubResponse:
        return StubResponse({"error": "denied"}, status_code=403, http_error=True)

    monkeypatch.setattr(exporter.requests, "get", fake_get)

    with pytest.raises(exporter.BloggerExportError) as exc_info:
        exporter.fetch_posts("https://example.blogspot.com", "key-123")

    message = str(exc_info.value)
    assert message == "Fetching blog metadata failed with HTTP 403."
    assert "key-123" not in message


def test_fetch_posts_fails_on_invalid_posts_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get(
        url: str,
        params: dict[str, Any],
        timeout: int,
    ) -> StubResponse:
        if url.endswith("/blogs/byurl"):
            return StubResponse({"id": "blog-123"})
        return StubResponse({"items": {"title": "not a list"}})

    monkeypatch.setattr(exporter.requests, "get", fake_get)

    with pytest.raises(exporter.BloggerExportError, match="invalid posts list"):
        exporter.fetch_posts("https://example.blogspot.com", "key-123")


def test_fetch_posts_wraps_request_failures_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get(
        url: str,
        params: dict[str, Any],
        timeout: int,
    ) -> StubResponse:
        raise exporter.requests.Timeout(f"timed out with key {params['key']}")

    monkeypatch.setattr(exporter.requests, "get", fake_get)

    with pytest.raises(exporter.BloggerExportError) as exc_info:
        exporter.fetch_posts("https://example.blogspot.com", "key-123")

    message = str(exc_info.value)
    assert "failed before receiving a response" in message
    assert "key-123" not in message


def test_save_markdown_writes_yaml_front_matter_and_content(tmp_path: Path) -> None:
    path = exporter.save_markdown(
        make_post("My Post", labels=["blogger", "markdown"]),
        tmp_path,
    )

    metadata, body = read_markdown(path)

    assert path.name == "2024-01-02_My_Post.md"
    assert metadata == {
        "title": "My Post",
        "date": "2024-01-02T03:04:05Z",
        "updated": "2024-01-03T04:05:06Z",
        "source_url": "https://example.blogspot.com/2024/01/post.html",
        "blogger_id": "post-123",
        "labels": ["blogger", "markdown"],
    }
    assert body == "# My Post\n\nHello **world**\n"


def test_save_markdown_sanitizes_filename_and_content(tmp_path: Path) -> None:
    path = exporter.save_markdown(make_post(" A/B: C? * D\tE\nF "), tmp_path)
    metadata, body = read_markdown(path)

    assert path.name == "2024-01-02_A_B_C_D_E_F.md"
    assert metadata["title"] == " A/B: C? * D\tE\nF "
    assert "Hello **world**\n" in body
    assert "Original URL" not in body


def test_save_markdown_preserves_readable_unicode(tmp_path: Path) -> None:
    path = exporter.save_markdown(make_post("你好 世界"), tmp_path)

    assert path.name == "2024-01-02_你好_世界.md"


def test_save_markdown_trims_long_filenames(tmp_path: Path) -> None:
    path = exporter.save_markdown(make_post("a" * 500), tmp_path)

    assert path.suffix == ".md"
    assert len(path.name.encode("utf-8")) <= exporter.MAX_FILENAME_BYTES


def test_save_markdown_skips_unchanged_existing_front_matter_file(
    tmp_path: Path,
) -> None:
    first_path = exporter.save_markdown(make_post("Same title"), tmp_path)
    second_path = exporter.save_markdown(make_post("Same title"), tmp_path)

    assert second_path == first_path
    assert list(tmp_path.glob("*.md")) == [first_path]


def test_save_markdown_writes_conflict_copy_for_changed_existing_post(
    tmp_path: Path,
) -> None:
    first_path = exporter.save_markdown(make_post("Same title", "first"), tmp_path)
    second_path = exporter.save_markdown(make_post("Same title", "second"), tmp_path)

    assert first_path.name == "2024-01-02_Same_title.md"
    assert second_path.name == "2024-01-02_Same_title_conflict.md"
    assert first_path.read_text(encoding="utf-8").endswith("first\n")
    assert second_path.read_text(encoding="utf-8").endswith("second\n")


def test_save_markdown_keeps_duplicate_titles_for_different_posts(
    tmp_path: Path,
) -> None:
    first_path = exporter.save_markdown(
        make_post("Same title", "first", post_id="post-1"),
        tmp_path,
    )
    second_path = exporter.save_markdown(
        make_post("Same title", "second", post_id="post-2"),
        tmp_path,
    )

    assert first_path.name == "2024-01-02_Same_title.md"
    assert second_path.name == "2024-01-02_Same_title_2.md"
    assert first_path.read_text(encoding="utf-8").endswith("first\n")
    assert second_path.read_text(encoding="utf-8").endswith("second\n")


def test_save_markdown_overwrites_changed_existing_post(tmp_path: Path) -> None:
    first_path = exporter.save_markdown(make_post("Same title", "first"), tmp_path)
    second_path = exporter.save_markdown(
        make_post("Same title", "second"),
        tmp_path,
        overwrite=True,
    )

    assert second_path == first_path
    assert list(tmp_path.glob("*.md")) == [first_path]
    assert first_path.read_text(encoding="utf-8").endswith("second\n")


def test_save_markdown_upgrades_exact_legacy_file_in_place(tmp_path: Path) -> None:
    legacy_path = tmp_path / "2024-01-02_Legacy.md"
    legacy_path.write_text(
        "# Legacy\n\n"
        "> Original URL: https://example.blogspot.com/2024/01/post.html\n\n"
        "old\n",
        encoding="utf-8",
    )

    path = exporter.save_markdown(make_post("Legacy", "old"), tmp_path)
    metadata, body = read_markdown(path)

    assert path == legacy_path
    assert metadata["blogger_id"] == "post-123"
    assert body == "# Legacy\n\nold\n"


def test_save_markdown_preserves_edited_legacy_file_with_conflict_copy(
    tmp_path: Path,
) -> None:
    legacy_path = tmp_path / "2024-01-02_Legacy.md"
    legacy_path.write_text("# Legacy\n\nlocal edit\n", encoding="utf-8")

    path = exporter.save_markdown(make_post("Legacy", "remote"), tmp_path)

    assert path.name == "2024-01-02_Legacy_conflict.md"
    assert legacy_path.read_text(encoding="utf-8") == "# Legacy\n\nlocal edit\n"
    assert path.read_text(encoding="utf-8").endswith("remote\n")
