# Reddit 评论爬虫（RSS 方案）

无需注册、无 API 凭证，通过 Reddit 公开 RSS 接口抓取评论。

## 快速开始

```bash
pip install requests
python reddit_scraper.py "URL" --proxy http://127.0.0.1:PORT
```

## 用法

```bash
# 基本用法（如网络环境需要代理）
python reddit_scraper.py "帖子URL" --proxy http://127.0.0.1:PORT

# 仅预览，不保存文件
python reddit_scraper.py "URL" --proxy http://127.0.0.1:PORT --preview-only

# 指定输出目录和文件名
python reddit_scraper.py "URL" --proxy http://127.0.0.1:PORT -o my_data --output-dir ./scraped

# 只生成 CSV
python reddit_scraper.py "URL" --proxy http://127.0.0.1:PORT --no-json

# 调整翻页次数（每页约 25 条，间隔 5 秒）
python reddit_scraper.py "URL" --proxy http://127.0.0.1:PORT --max-pages 5

# 查看帮助
python reddit_scraper.py --help
```

## 输出

`output/` 目录下生成：
- `.json` — 完整数据（帖子信息 + 评论列表）
- `.csv`  — 评论表格，字段：comment_id、author、body、published

## 说明

- 基于 Reddit RSS feed，每页约 25 条顶层评论
- 不包含嵌套回复（RSS 不提供嵌套结构）
- 翻页间隔 5 秒，受 Reddit 频率限制
- 如需**完整嵌套评论**，请用 PRAW 方案
