# Blogspot to Markdown Exporter

Export posts from a Blogspot blog to Markdown files. The tool fetches posts with the Blogger API, converts HTML content to Markdown, and writes one Markdown file per post.

## Features

- Fetches posts from a specified Blogspot blog
- Follows Blogger API pagination to export all available posts
- Converts HTML content to Markdown
- Saves each post as a separate Markdown file
- Writes YAML front matter with the post title, dates, source URL, Blogger ID, and labels
- Sanitizes filenames and avoids overwriting local edits during repeated exports

## Prerequisites

- `uv`
- A Google Cloud project with the Blogger API enabled
- A Blogger API key

## Installation

```sh
git clone https://github.com/p0n1/blogspot_to_markdown.git
cd blogspot_to_markdown
uv sync
```

`uv` reads dependencies from `pyproject.toml` and creates a local `.venv` automatically.

## Usage

Run the installed CLI with `uv`:

```sh
uv run blogspot-to-markdown --blog-url <your-blog-url> --api-key <your-api-key> [--output-dir <output-directory>]
```

Arguments:

- `--blog-url`: URL of the Blogspot blog to export
- `--api-key`: Blogger API key. If omitted, the CLI reads `BLOGGER_API_KEY`
- `--output-dir`: directory where Markdown files will be saved, defaulting to `markdown_posts`
- `--overwrite`: replace changed existing exports instead of writing conflict copies

Example:

```sh
uv run blogspot-to-markdown --blog-url https://example.blogspot.com --api-key YOUR_API_KEY --output-dir my_blog_posts
```

Or provide the key with an environment variable:

```sh
BLOGGER_API_KEY=YOUR_API_KEY uv run blogspot-to-markdown --blog-url https://example.blogspot.com --output-dir my_blog_posts
```

For local use, copy the tracked example file and store the key in an ignored `.env` file:

```sh
cp .env.example .env
```

Then edit `.env`:

```sh
BLOGGER_API_KEY=YOUR_API_KEY
```

The CLI reads `.env` automatically from the current directory when `--api-key` and the environment variable are omitted:

```sh
uv run blogspot-to-markdown --blog-url https://example.blogspot.com --output-dir my_blog_posts
```

The legacy script entry point also works:

```sh
uv run python main.py --blog-url https://example.blogspot.com --api-key YOUR_API_KEY
```

## Output

The exporter creates Markdown files in the specified output directory. The filename format is:

```text
YYYY-MM-DD_Post_Title.md
```

Unsafe path characters are replaced with underscores, repeated whitespace is collapsed, readable Unicode is preserved, and very long filenames are trimmed. If two posts would produce the same filename, later files receive suffixes such as `_2` before `.md`.

Each Markdown file contains YAML front matter followed by the post title as a heading and the converted Markdown content with a final newline:

```markdown
---
title: My Post
date: '2024-01-02T03:04:05Z'
updated: '2024-01-03T04:05:06Z'
source_url: https://example.blogspot.com/2024/01/post.html
blogger_id: '1234567890'
labels:
- blogger
- markdown
---

# My Post

Hello **world**
```

## Repeated exports

Repeated exports are idempotent when Blogger post IDs are available in front matter:

- Unchanged posts are skipped.
- Changed posts keep the existing file and write a `_conflict` copy by default.
- `--overwrite` replaces the matched existing file with the latest generated content.
- Legacy files in the old heading plus `Original URL` format are upgraded in place only when they exactly match the old generated output.
