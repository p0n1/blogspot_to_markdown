from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from .exporter import (
    BloggerExportError,
    archive_existing_markdown_assets,
    export_blog,
)

API_KEY_ENV_VAR = "BLOGGER_API_KEY"
ENV_FILE = Path(".env")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export Blogspot posts to Markdown files.",
    )
    parser.add_argument(
        "--blog-url",
        help="URL of the Blogspot blog to export.",
    )
    parser.add_argument(
        "--api-key",
        help=f"Blogger API key. Defaults to ${API_KEY_ENV_VAR} or .env when omitted.",
    )
    parser.add_argument(
        "--output-dir",
        default=Path("markdown_posts"),
        type=Path,
        help="Directory to save Markdown files.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite changed existing exports instead of writing conflict copies.",
    )
    parser.add_argument(
        "--archive-assets",
        action="store_true",
        help="Download remote images and rewrite Markdown image links to local files.",
    )
    parser.add_argument(
        "--archive-existing-assets",
        action="store_true",
        help="Only scan existing Markdown files in --output-dir and localize images.",
    )
    return parser


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler()],
    )


def read_api_key_from_env_file(path: Path = ENV_FILE) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key.removeprefix("export ").strip()
        if key != API_KEY_ENV_VAR:
            continue

        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value or None

    return None


def resolve_api_key(cli_value: str | None) -> str | None:
    return cli_value or os.environ.get(API_KEY_ENV_VAR) or read_api_key_from_env_file()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging()
    if args.archive_existing_assets:
        archive_existing_markdown_assets(args.output_dir, overwrite=args.overwrite)
        return 0

    if not args.blog_url:
        parser.error("--blog-url is required unless --archive-existing-assets is used.")

    api_key = resolve_api_key(args.api_key)
    if not api_key:
        parser.error(
            f"--api-key is required unless {API_KEY_ENV_VAR} is set "
            "in the environment or .env."
        )

    try:
        export_blog(
            args.blog_url,
            api_key,
            args.output_dir,
            overwrite=args.overwrite,
            archive_assets=args.archive_assets,
        )
    except BloggerExportError as exc:
        logging.error("%s", exc)
        return 1
    return 0
