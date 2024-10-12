import os
import requests
import argparse
import logging
from markdownify import markdownify as md

def fetch_posts(blog_url, api_key):
    # Extract the blog ID
    blog_id_url = f'https://www.googleapis.com/blogger/v3/blogs/byurl?key={api_key}&url={blog_url}'
    blog_id_response = requests.get(blog_id_url)
    blog_id_response.raise_for_status()
    blog_id = blog_id_response.json()['id']
    
    # Fetch posts
    posts_url = f'https://www.googleapis.com/blogger/v3/blogs/{blog_id}/posts?key={api_key}&maxResults=500'
    response = requests.get(posts_url)
    response.raise_for_status()
    return response.json().get('items', [])

def save_markdown(post, directory):
    title = post['title']
    content_html = post['content']
    content_md = md(content_html)
    publish_date = post['published'][:10]  # Extract YYYY-MM-DD from the published date
    filename = f"{publish_date}_{title.replace(' ', '_').replace('/', '_')}.md"
    filepath = os.path.join(directory, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(f"# {title}\n\n{content_md}")

def main():
    parser = argparse.ArgumentParser(description="Export Blogspot posts to Markdown files.")
    parser.add_argument('--blog-url', required=True, help='URL of the Blogspot blog to export.')
    parser.add_argument('--api-key', required=True, help='Blogger API key.')
    parser.add_argument('--output-dir', default='markdown_posts', help='Directory to save Markdown files.')
    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler()
        ]
    )

    logging.info("Starting the export process.")
    os.makedirs(args.output_dir, exist_ok=True)
    posts = fetch_posts(args.blog_url, args.api_key)
    logging.info(f"Fetched {len(posts)} posts.")

    for idx, post in enumerate(posts, start=1):
        logging.info(f"Exporting post {idx}/{len(posts)}: {post['title']}")
        save_markdown(post, args.output_dir)
        logging.info(f"Successfully exported: {post['title']}")

    logging.info(f"Exported {len(posts)} posts to the '{args.output_dir}' directory.")

if __name__ == "__main__":
    main()
