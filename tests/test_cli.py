from __future__ import annotations

import logging
from pathlib import Path

import pytest

from blogspot_to_markdown import cli


def test_main_uses_explicit_api_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str, Path, bool]] = []

    def fake_export_blog(
        blog_url: str,
        api_key: str,
        output_dir: Path,
        *,
        overwrite: bool = False,
    ) -> int:
        calls.append((blog_url, api_key, output_dir, overwrite))
        return 1

    monkeypatch.setenv(cli.API_KEY_ENV_VAR, "env-key")
    (tmp_path / ".env").write_text("BLOGGER_API_KEY=file-key\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "export_blog", fake_export_blog)

    result = cli.main(
        [
            "--blog-url",
            "https://example.blogspot.com",
            "--api-key",
            "explicit-key",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert result == 0
    assert calls == [("https://example.blogspot.com", "explicit-key", tmp_path, False)]


def test_main_uses_api_key_from_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, Path, bool]] = []

    def fake_export_blog(
        blog_url: str,
        api_key: str,
        output_dir: Path,
        *,
        overwrite: bool = False,
    ) -> int:
        calls.append((blog_url, api_key, output_dir, overwrite))
        return 1

    monkeypatch.setenv(cli.API_KEY_ENV_VAR, "env-key")
    (tmp_path / ".env").write_text("BLOGGER_API_KEY=file-key\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "export_blog", fake_export_blog)

    result = cli.main(
        [
            "--blog-url",
            "https://example.blogspot.com",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert result == 0
    assert calls == [("https://example.blogspot.com", "env-key", tmp_path, False)]


def test_main_uses_api_key_from_env_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, Path, bool]] = []

    def fake_export_blog(
        blog_url: str,
        api_key: str,
        output_dir: Path,
        *,
        overwrite: bool = False,
    ) -> int:
        calls.append((blog_url, api_key, output_dir, overwrite))
        return 1

    monkeypatch.delenv(cli.API_KEY_ENV_VAR, raising=False)
    (tmp_path / ".env").write_text(
        "# local secrets\nexport BLOGGER_API_KEY='file-key'\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "export_blog", fake_export_blog)

    result = cli.main(
        [
            "--blog-url",
            "https://example.blogspot.com",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert result == 0
    assert calls == [("https://example.blogspot.com", "file-key", tmp_path, False)]


def test_main_passes_overwrite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str, Path, bool]] = []

    def fake_export_blog(
        blog_url: str,
        api_key: str,
        output_dir: Path,
        *,
        overwrite: bool = False,
    ) -> int:
        calls.append((blog_url, api_key, output_dir, overwrite))
        return 1

    monkeypatch.setattr(cli, "export_blog", fake_export_blog)

    result = cli.main(
        [
            "--blog-url",
            "https://example.blogspot.com",
            "--api-key",
            "explicit-key",
            "--output-dir",
            str(tmp_path),
            "--overwrite",
        ]
    )

    assert result == 0
    assert calls == [("https://example.blogspot.com", "explicit-key", tmp_path, True)]


def test_main_fails_when_api_key_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv(cli.API_KEY_ENV_VAR, raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--blog-url", "https://example.blogspot.com"])

    assert exc_info.value.code == 2
    assert "BLOGGER_API_KEY" in capsys.readouterr().err


def test_main_returns_one_on_export_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fake_export_blog(
        blog_url: str,
        api_key: str,
        output_dir: Path,
        *,
        overwrite: bool = False,
    ) -> int:
        raise cli.BloggerExportError("Fetching blog metadata failed with HTTP 403.")

    monkeypatch.setattr(cli, "export_blog", fake_export_blog)

    with caplog.at_level(logging.ERROR):
        result = cli.main(
            [
                "--blog-url",
                "https://example.blogspot.com",
                "--api-key",
                "explicit-key",
            ]
        )

    assert result == 1
    assert "Fetching blog metadata failed with HTTP 403." in caplog.text
    assert "explicit-key" not in caplog.text
