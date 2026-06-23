from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .exporter import export_blog


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export Blogspot posts to Markdown files.",
    )
    parser.add_argument(
        "--blog-url",
        required=True,
        help="URL of the Blogspot blog to export.",
    )
    parser.add_argument(
        "--api-key",
        required=True,
        help="Blogger API key.",
    )
    parser.add_argument(
        "--output-dir",
        default=Path("markdown_posts"),
        type=Path,
        help="Directory to save Markdown files.",
    )
    return parser


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler()],
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging()
    export_blog(args.blog_url, args.api_key, args.output_dir)
    return 0
