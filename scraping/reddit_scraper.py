"""
Reddit 评论爬虫（RSS 方案，无需 API 凭证）
通过 Reddit 公开 RSS 订阅接口抓取评论，保存为 CSV/JSON。

用法:
    python reddit_scraper.py "https://www.reddit.com/r/NoStupidQuestions/comments/xxxxx/..."
    python reddit_scraper.py "URL" --proxy http://127.0.0.1:PORT

特点:
    - 无需注册、无需 API 凭证
    - 基于 Reddit 官方 RSS 接口
    - 支持代理
    - 输出 CSV + JSON
    - 自动限速，避免 429
"""

import argparse
import csv
import json
import logging
import os
import re
import sys
import time
import html
from datetime import datetime, timezone
from typing import Optional
from xml.etree import ElementTree as ET

import requests

# ============================================================================
# 配置
# ============================================================================

USER_AGENT = "Mozilla/5.0 (compatible; RedditScraper/1.0)"

# 请求间隔（秒），RSS 翻页需要较长间隔避免 429
REQUEST_DELAY = 5.0

# 每次 RSS 请求返回的评论数（Reddit RSS 默认限制，设多少都可能只返回 25 左右）
RSS_COMMENT_LIMIT = 100

# 保存目录（默认写到项目根的 scraped/，不依赖运行时 cwd）
from pathlib import Path as _Path
OUTPUT_DIR = str(_Path(__file__).resolve().parents[1] / "scraped")

# ============================================================================
# 日志
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("reddit_scraper")

# ============================================================================
# RSS 解析命名空间
# ============================================================================

XML_NS = {
    "atom": "http://www.w3.org/2005/Atom",
}

# ============================================================================
# 工具函数
# ============================================================================


def extract_post_id(url: str) -> str:
    """从 URL 中提取帖子 ID。"""
    match = re.search(r"/comments/([a-z0-9]+)", url, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"redd\.it/([a-z0-9]+)", url, re.IGNORECASE)
    if match:
        return match.group(1)
    raise ValueError(f"无法从 URL 中提取帖子 ID: {url}")


def extract_subreddit(url: str) -> str:
    """从 URL 中提取板块名。"""
    match = re.search(r"/r/([a-zA-Z0-9_]+)", url)
    if match:
        return match.group(1)
    return "unknown"


def iso_to_filename(iso_str: Optional[str]) -> str:
    """把 RSS 的 ISO 时间转成安全的文件名（YYYY-MM-DD_HHMMSS）。"""
    if not iso_str:
        return "unknown_date"
    # 匹配开头日期时间部分，如 2026-08-11T03:17:43
    m = re.match(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2}):(\d{2})", iso_str)
    if m:
        return f"{m.group(1)}_{m.group(2)}{m.group(3)}{m.group(4)}"
    # 退而求其次，只取日期
    m = re.match(r"(\d{4}-\d{2}-\d{2})", iso_str)
    if m:
        return m.group(1)
    # 兜底：清理非法字符
    return re.sub(r"[^\w\-]", "_", iso_str)


def clean_html(raw: Optional[str]) -> str:
    """清理 HTML 标签，提取纯文本。"""
    if not raw:
        return ""
    # 去掉 HTML 标签
    text = re.sub(r"<[^>]+>", "", raw)
    # 去掉 Reddit 的 <!-- SC_OFF --> <!-- SC_ON -->
    text = text.replace("<!-- SC_OFF -->", "").replace("<!-- SC_ON -->", "")
    # 解码 HTML 实体
    text = html.unescape(text)
    # 清理多余空白
    text = re.sub(r"\n\s*\n", "\n\n", text)
    return text.strip()


# ============================================================================
# RSS 爬虫
# ============================================================================


class RedditRSSScraper:
    """基于 RSS feed 的 Reddit 评论爬虫。"""

    def __init__(self, proxy: Optional[str] = None, delay: float = REQUEST_DELAY):
        self.proxy = proxy
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

        if proxy:
            self.session.proxies.update({"http": proxy, "https": proxy})
            logger.info(f"代理: {proxy}")

        self._last_request = 0.0
        self._request_count = 0

    def _wait(self):
        """限速等待。"""
        elapsed = time.time() - self._last_request
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request = time.time()
        self._request_count += 1

    def _get(self, url: str) -> requests.Response:
        """带限速和重试的 GET 请求。"""
        max_retries = 3
        for attempt in range(max_retries):
            self._wait()
            try:
                resp = self.session.get(url, timeout=30)
                if resp.status_code == 429:
                    wait = min(120, (attempt + 1) * 20)
                    logger.warning(f"被限速 (429)，等待 {wait} 秒...")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp
            except requests.exceptions.HTTPError:
                logger.error(f"HTTP {resp.status_code}")
                if attempt == max_retries - 1:
                    raise
            except requests.exceptions.RequestException as e:
                logger.error(f"请求失败: {e}")
                if attempt == max_retries - 1:
                    raise
                time.sleep(3)
        raise RuntimeError(f"请求失败: {url}")

    def scrape(self, post_url: str, max_pages: int = 20) -> dict:
        """
        爬取帖子及评论。

        参数:
            post_url: Reddit 帖子 URL
            max_pages: 最多翻多少页 RSS（每页约 25 条评论）

        返回:
            包含帖子信息和评论列表的字典
        """
        post_id = extract_post_id(post_url)
        subreddit = extract_subreddit(post_url)

        logger.info(f"帖子: r/{subreddit} | ID: {post_id}")

        all_comments = []
        seen_ids = set()
        post_info = None
        after_id = None

        for page in range(max_pages):
            # 构造 RSS URL
            rss_url = (
                f"https://www.reddit.com/r/{subreddit}"
                f"/comments/{post_id}/.rss"
                f"?limit={RSS_COMMENT_LIMIT}"
            )
            if after_id:
                rss_url += f"&after={after_id}"

            logger.info(f"请求第 {page + 1} 页...")
            resp = self._get(rss_url)

            # 解析 XML
            root = ET.fromstring(resp.text)
            entries = root.findall("atom:entry", XML_NS)

            if not entries:
                logger.info("没有更多条目，停止翻页")
                break

            new_this_page = 0

            for entry in entries:
                entry_id = entry.find("atom:id", XML_NS)
                entry_id = entry_id.text if entry_id is not None else ""

                # 跳过已见过的
                if entry_id and entry_id in seen_ids:
                    continue
                if entry_id:
                    seen_ids.add(entry_id)

                author_el = entry.find("atom:author/atom:name", XML_NS)
                author = author_el.text if author_el is not None else "[deleted]"

                title_el = entry.find("atom:title", XML_NS)
                title = title_el.text if title_el is not None else ""

                content_el = entry.find("atom:content", XML_NS)
                raw_content = content_el.text if content_el is not None else ""

                updated_el = entry.find("atom:updated", XML_NS)
                updated = updated_el.text if updated_el is not None else ""

                published_el = entry.find("atom:published", XML_NS)
                published = published_el.text if published_el is not None else updated

                # 判断是帖子还是评论
                # 帖子: title 不含 "/u/"
                # 评论: title 格式为 "/u/username on ..."
                is_post = not author or ("/u/" not in title)

                if is_post:
                    # 这是帖子本身
                    if post_info is None:
                        post_info = {
                            "post_id": post_id,
                            "post_title": title.strip() if title else "",
                            "post_body": clean_html(raw_content),
                            "post_author": author.replace("/u/", "") if author else "[deleted]",
                            "subreddit": subreddit,
                            "post_url": post_url,
                            "published": published,
                            "scraped_at": datetime.now(timezone.utc).strftime(
                                "%Y-%m-%d %H:%M:%S UTC"
                            ),
                        }
                    continue

                # 这是评论
                # 从 title 中提取作者
                comment_author = ""
                title_match = re.match(r"/u/(\S+)", title)
                if title_match:
                    comment_author = title_match.group(1)

                comment = {
                    "comment_id": entry_id.split("/")[-1] if entry_id else "",
                    "entry_id": entry_id,
                    "author": comment_author or author.replace("/u/", ""),
                    "body": clean_html(raw_content),
                    "depth": 0,  # RSS 不提供嵌套信息，统一 depth=0
                    "published": updated,
                    "page": page + 1,
                }
                all_comments.append(comment)
                new_this_page += 1

            logger.info(f"  第 {page + 1} 页: {new_this_page} 条新评论（累计 {len(all_comments)}）")

            # 用最后一条的 ID 做翻页游标
            if entries:
                last_id_el = entries[-1].find("atom:id", XML_NS)
                if last_id_el is not None:
                    after_id = last_id_el.text.split("/")[-1]

            # 如果这页没有新评论，说明翻到头了
            if new_this_page == 0:
                break

        if post_info is None:
            post_info = {
                "post_id": post_id,
                "post_title": "(无法获取)",
                "post_body": "",
                "post_author": "[unknown]",
                "subreddit": subreddit,
                "post_url": post_url,
                "published": "",
                "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            }

        logger.info(f"完成！总评论数: {len(all_comments)}")

        return {
            **post_info,
            "total_scraped": len(all_comments),
            "comments": all_comments,
        }


# ============================================================================
# 输出
# ============================================================================


def save_json(data: dict, path: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"JSON: {path}")


def save_csv(comments: list, path: str):
    if not comments:
        logger.warning("没有评论，跳过 CSV")
        return

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fields = [
        "comment_id", "author", "body", "depth", "published", "page",
    ]

    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(comments)

    logger.info(f"CSV: {path} ({len(comments)} 条)")


def preview(data: dict, n: int = 10):
    print()
    print("=" * 60)
    print(f"帖子: {data.get('post_title', 'N/A')}")
    print(f"作者: u/{data.get('post_author', 'N/A')}")
    print(f"板块: r/{data.get('subreddit', 'N/A')}")
    print(f"抓取评论: {data.get('total_scraped', 0)} 条")
    print("=" * 60)

    comments = data.get("comments", [])
    if not comments:
        print("(无评论)")
        return

    print(f"\n前 {min(n, len(comments))} 条:\n")
    for i, c in enumerate(comments[:n], 1):
        body = c.get("body", "")
        preview_text = body[:200] + "..." if len(body) > 200 else body
        print(f"  [{i}] u/{c['author']}")
        print(f"      {preview_text}")
        print()


# ============================================================================
# 命令行
# ============================================================================


def main():
    # 修复 Windows 终端 GBK 编码无法打印 emoji 的问题
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        description="Reddit 评论爬虫（RSS 方案，无需 API 凭证）"
    )
    parser.add_argument("url", nargs="?", help="Reddit 帖子 URL")
    parser.add_argument("-o", "--output", default=None, help="输出文件名前缀")
    parser.add_argument("--output-dir", default=OUTPUT_DIR, help="输出目录")
    parser.add_argument("--proxy", default=None, help="代理地址，如 http://127.0.0.1:PORT")
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY, help="请求间隔秒数")
    parser.add_argument("--max-pages", type=int, default=20, help="最大翻页数")
    parser.add_argument("--no-csv", action="store_true")
    parser.add_argument("--no-json", action="store_true")
    parser.add_argument("--preview-only", action="store_true", help="仅预览")
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not args.url:
        print("用法: python reddit_scraper.py \"https://www.reddit.com/r/.../comments/.../\"")
        print("更多: python reddit_scraper.py --help")
        sys.exit(0)

    scraper = RedditRSSScraper(proxy=args.proxy, delay=args.delay)

    try:
        data = scraper.scrape(args.url, max_pages=args.max_pages)
        preview(data)

        if not args.preview_only:
            # 默认用帖子发布时间命名（如 2023-02-08_031743.json）
            post_id = data.get("post_id", "output")
            published = data.get("published", "")
            default_prefix = iso_to_filename(published) or post_id
            prefix = args.output or default_prefix

            if not args.no_json:
                save_json(data, os.path.join(args.output_dir, f"{prefix}.json"))

            if not args.no_csv:
                save_csv(data["comments"], os.path.join(args.output_dir, f"{prefix}.csv"))

            logger.info("完成！")

    except ValueError as e:
        logger.error(f"URL 错误: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"错误: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()
