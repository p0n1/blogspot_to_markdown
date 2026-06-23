from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from blogspot_to_markdown import exporter


class StubResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


def make_post(title: str, content: str = "<p>Hello <strong>world</strong></p>") -> dict[str, str]:
    return {
        "title": title,
        "content": content,
        "published": "2024-01-02T03:04:05Z",
        "url": "https://example.blogspot.com/2024/01/post.html",
    }


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


def test_save_markdown_sanitizes_filename_and_content(tmp_path: Path) -> None:
    path = exporter.save_markdown(make_post(" A/B: C? * D\tE\nF "), tmp_path)

    assert path.name == "2024-01-02_A_B_C_D_E_F.md"
    assert path.read_text(encoding="utf-8") == (
        "#  A/B: C? * D\tE\n"
        "F \n\n"
        "> Original URL: https://example.blogspot.com/2024/01/post.html\n\n"
        "Hello **world**\n"
    )


def test_save_markdown_preserves_readable_unicode(tmp_path: Path) -> None:
    path = exporter.save_markdown(make_post("你好 世界"), tmp_path)

    assert path.name == "2024-01-02_你好_世界.md"


def test_save_markdown_trims_long_filenames(tmp_path: Path) -> None:
    path = exporter.save_markdown(make_post("a" * 500), tmp_path)

    assert path.suffix == ".md"
    assert len(path.name.encode("utf-8")) <= exporter.MAX_FILENAME_BYTES


def test_save_markdown_avoids_overwriting_existing_files(tmp_path: Path) -> None:
    first_path = exporter.save_markdown(make_post("Same title", "first"), tmp_path)
    second_path = exporter.save_markdown(make_post("Same title", "second"), tmp_path)

    assert first_path.name == "2024-01-02_Same_title.md"
    assert second_path.name == "2024-01-02_Same_title_2.md"
    assert first_path.read_text(encoding="utf-8").endswith("first\n")
    assert second_path.read_text(encoding="utf-8").endswith("second\n")
