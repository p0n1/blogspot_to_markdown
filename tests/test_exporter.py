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
        content: bytes = b"",
        headers: dict[str, str] | None = None,
        json_error: ValueError | None = None,
        http_error: bool = False,
    ) -> None:
        self.payload = payload
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
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


def read_asset_manifest(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(
        (path / exporter.ASSET_DIR_NAME / exporter.ASSET_MANIFEST_NAME).read_text(
            encoding="utf-8",
        )
    )
    assert isinstance(payload, dict)
    return payload


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


def test_save_markdown_archives_remote_images(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    image_url = "https://example.com/images/chart.png"
    image_bytes = b"png-bytes"

    def fake_get(url: str, timeout: int) -> StubResponse:
        assert url == image_url
        assert timeout == exporter.REQUEST_TIMEOUT_SECONDS
        return StubResponse(
            {},
            content=image_bytes,
            headers={"Content-Type": "image/png"},
        )

    monkeypatch.setattr(exporter.requests, "get", fake_get)

    path = exporter.save_markdown(
        make_post("With Image", f'<p><img alt="Chart" src="{image_url}"></p>'),
        tmp_path,
        archive_assets=True,
    )

    text = path.read_text(encoding="utf-8")
    manifest = read_asset_manifest(tmp_path)
    asset = manifest["assets"][0]

    assert manifest["version"] == 1
    assert asset["original_url"] == image_url
    assert asset["status"] == "downloaded"
    assert asset["content_type"] == "image/png"
    assert asset["size_bytes"] == len(image_bytes)
    assert asset["sha256"]
    assert asset["local_path"].startswith("_assets/post-123/")
    assert asset["local_path"].endswith("_chart.png")
    assert (tmp_path / asset["local_path"]).read_bytes() == image_bytes
    assert image_url not in text
    assert f"![Chart]({asset['local_path']})" in text


def test_save_markdown_reuses_archived_images_on_rerun(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    image_url = "https://example.com/images/chart.png"
    calls: list[str] = []

    def fake_get(url: str, timeout: int) -> StubResponse:
        calls.append(url)
        return StubResponse(
            {},
            content=b"image",
            headers={"Content-Type": "image/png"},
        )

    monkeypatch.setattr(exporter.requests, "get", fake_get)
    post = make_post("With Image", f'<img src="{image_url}">')

    first_path = exporter.save_markdown(post, tmp_path, archive_assets=True)
    second_path = exporter.save_markdown(post, tmp_path, archive_assets=True)

    assert second_path == first_path
    assert calls == [image_url]
    assert len(list((tmp_path / exporter.ASSET_DIR_NAME).glob("post-123/*.png"))) == 1


def test_save_markdown_archives_outer_blogger_image_link(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    thumbnail_url = "https://blogger.googleusercontent.com/img/x/s320/thumb.jpg"
    full_url = "https://blogger.googleusercontent.com/img/x/s1600/full.jpg"
    calls: list[str] = []

    def fake_get(url: str, timeout: int) -> StubResponse:
        calls.append(url)
        return StubResponse(
            {},
            content=b"full-image",
            headers={"Content-Type": "image/jpeg"},
        )

    monkeypatch.setattr(exporter.requests, "get", fake_get)

    path = exporter.save_markdown(
        make_post(
            "Linked Image",
            f'<a href="{full_url}"><img alt="Full" src="{thumbnail_url}"></a>',
        ),
        tmp_path,
        archive_assets=True,
    )

    text = path.read_text(encoding="utf-8")
    asset = read_asset_manifest(tmp_path)["assets"][0]

    assert calls == [full_url]
    assert asset["original_url"] == full_url
    assert f"[![Full]({asset['local_path']})]({asset['local_path']})" in text
    assert thumbnail_url not in text
    assert full_url not in text


def test_save_markdown_follows_html_image_wrapper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    wrapper_url = "https://blogger.googleusercontent.com/img/x/s1600-h/photo.jpg"
    resolved_url = "https://lh3.googleusercontent.com/blogger_img/photo"
    calls: list[str] = []

    def fake_get(url: str, timeout: int) -> StubResponse:
        calls.append(url)
        if url == wrapper_url:
            return StubResponse(
                {},
                content=f'<html><body><img src="{resolved_url}"></body></html>'.encode(),
                headers={"Content-Type": "text/html"},
            )
        assert url == resolved_url
        return StubResponse(
            {},
            content=b"\xff\xd8\xffimage",
            headers={"Content-Type": "image/jpeg"},
        )

    monkeypatch.setattr(exporter.requests, "get", fake_get)

    path = exporter.save_markdown(
        make_post("Wrapped Image", f'<img src="{wrapper_url}">'),
        tmp_path,
        archive_assets=True,
    )

    asset = read_asset_manifest(tmp_path)["assets"][0]

    assert calls == [wrapper_url, resolved_url]
    assert asset["original_url"] == wrapper_url
    assert asset["content_type"] == "image/jpeg"
    assert (tmp_path / asset["local_path"]).read_bytes() == b"\xff\xd8\xffimage"
    assert wrapper_url not in path.read_text(encoding="utf-8")


def test_save_markdown_keeps_non_image_link_around_local_image(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    image_url = "https://example.com/cover.jpg"
    link_url = "https://example.com/book"

    def fake_get(url: str, timeout: int) -> StubResponse:
        assert url == image_url
        return StubResponse(
            {},
            content=b"cover",
            headers={"Content-Type": "image/jpeg"},
        )

    monkeypatch.setattr(exporter.requests, "get", fake_get)

    path = exporter.save_markdown(
        make_post("Book", f'<a href="{link_url}"><img src="{image_url}"></a>'),
        tmp_path,
        archive_assets=True,
    )

    text = path.read_text(encoding="utf-8")
    asset = read_asset_manifest(tmp_path)["assets"][0]

    assert f"[![]({asset['local_path']})]({link_url})" in text
    assert image_url not in text


def test_save_markdown_records_asset_failures_without_failing_export(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    image_url = "https://example.com/missing.png"

    def fake_get(url: str, timeout: int) -> StubResponse:
        raise exporter.requests.Timeout("timed out")

    monkeypatch.setattr(exporter.requests, "get", fake_get)

    path = exporter.save_markdown(
        make_post("Missing Image", f'<img src="{image_url}">'),
        tmp_path,
        archive_assets=True,
    )

    text = path.read_text(encoding="utf-8")
    asset = read_asset_manifest(tmp_path)["assets"][0]

    assert image_url in text
    assert asset == {
        "original_url": image_url,
        "status": "failed",
        "error": "Image download failed before receiving a usable response.",
    }


def test_archive_existing_markdown_assets_rewrites_legacy_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    image_url = "https://pbs.twimg.com/media/GxnNURIWkAAZpZ_?format=jpg&name=large"
    path = tmp_path / "Legacy.md"
    path.write_text(f"# Legacy\n\n![Chart]({image_url})\n", encoding="utf-8")

    def fake_get(url: str, timeout: int) -> StubResponse:
        assert url == image_url
        return StubResponse(
            {},
            content=b"jpg-bytes",
            headers={"Content-Type": "image/jpeg"},
        )

    monkeypatch.setattr(exporter.requests, "get", fake_get)

    summary = exporter.archive_existing_markdown_assets(tmp_path, overwrite=True)

    text = path.read_text(encoding="utf-8")
    asset = read_asset_manifest(tmp_path)["assets"][0]

    assert summary.scanned == 1
    assert summary.rewritten == 1
    assert summary.downloaded == 1
    assert asset["local_path"].startswith("_assets/Legacy/")
    assert asset["local_path"].endswith(".jpg")
    assert image_url not in text
    assert f"![Chart]({asset['local_path']})" in text


def test_archive_existing_markdown_assets_preserves_original_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    image_url = "https://example.com/localize.png"
    path = tmp_path / "Legacy.md"
    path.write_text(f"# Legacy\n\n![Chart]({image_url})\n", encoding="utf-8")

    def fake_get(url: str, timeout: int) -> StubResponse:
        return StubResponse(
            {},
            content=b"png-bytes",
            headers={"Content-Type": "image/png"},
        )

    monkeypatch.setattr(exporter.requests, "get", fake_get)

    summary = exporter.archive_existing_markdown_assets(tmp_path)
    conflict_path = tmp_path / "Legacy_conflict.md"

    assert summary.conflicts == 1
    assert image_url in path.read_text(encoding="utf-8")
    assert image_url not in conflict_path.read_text(encoding="utf-8")


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
