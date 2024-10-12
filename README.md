
# Blogspot to Markdown Exporter

This Python script allows you to export posts from a Blogspot blog to Markdown files. It uses the Blogger API to fetch blog posts and converts them to Markdown format, saving each post as a separate file.

## Features

- Fetches all posts from a specified Blogspot blog
- Converts HTML content to Markdown
- Saves each post as a separate Markdown file
- Includes post title and publish date in the filename

## Prerequisites

Before you begin, ensure you have met the following requirements:

- Python 3.6 or higher
- A Google Cloud project with the Blogger API enabled
- A Blogger API key

## Installation

1. Clone this repository:
   ```
   git clone https://github.com/p0n1/blogspot_to_markdown.git
   cd blogspot_to_markdown
   ```

2. Create a virtual environment and activate it:
   ```
   python -m venv venv
   source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
   ```

3. Install the required packages:
   ```
   pip install -r requirements.txt
   ```

## Usage

To use the Blogspot to Markdown Exporter, run the script with the following command-line arguments:

```
python main.py --blog-url <your-blog-url> --api-key <your-api-key> [--output-dir <output-directory>]


- `--blog-url`: The URL of your Blogspot blog (required)
- `--api-key`: Your Blogger API key (required)
- `--output-dir`: The directory where Markdown files will be saved (optional, default: 'markdown_posts')
```

Example:

```
python main.py --blog-url https://example.blogspot.com --api-key YOUR_API_KEY --output-dir my_blog_posts
```


This command will:
1. Fetch all posts from the specified blog
2. Convert each post to Markdown format
3. Save the Markdown files in the 'my_blog_posts' directory

## Output

The script will create Markdown files for each blog post in the specified output directory. The filename format is:

```
YYYY-MM-DD_Post_Title.md
```

Each Markdown file will contain the post title as a heading, followed by the post content in Markdown format.