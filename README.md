# Blogspot to Markdown Exporter

Export posts from a Blogspot blog to Markdown files. The tool fetches posts with the Blogger API, converts HTML content to Markdown, and writes one Markdown file per post.

## Features

- Fetches posts from a specified Blogspot blog
- Converts HTML content to Markdown
- Saves each post as a separate Markdown file
- Includes the post title, publish date, and original URL

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
- `--api-key`: Blogger API key
- `--output-dir`: directory where Markdown files will be saved, defaulting to `markdown_posts`

Example:

```sh
uv run blogspot-to-markdown --blog-url https://example.blogspot.com --api-key YOUR_API_KEY --output-dir my_blog_posts
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

Each Markdown file contains the post title as a heading, the original post URL, and the converted Markdown content.
