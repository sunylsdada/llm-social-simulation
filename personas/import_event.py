"""
把用户提供的评论 CSV 导入成事件包规范表。

输入：events/<id>/input/raw_comments.csv（或 --input 指定）
输出：events/<id>/input/comments.csv
      events/<id>/interim/import_report.json

支持中文列名映射、BOM/GBK 编码、ISO/中文/epoch 时间、去重、
父评论 ID 重映射和窗口过滤。不调用 LLM。

用法:
    python -m personas.import_event --event my_event --dry-run
    python -m personas.import_event --event my_event
    python -m personas.import_event --event my_event --input some.csv --force
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simulation.infra.event import event_paths, load_event

CANONICAL_COLUMNS = [
    "comment_id", "author", "body", "published", "published_date",
    "post_id", "post_title", "parent_id", "depth", "channel", "source",
    "likes", "source_file",
]

DEFAULT_TIME_FORMATS = [
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y年%m月%d日 %H:%M:%S",
    "%Y年%m月%d日 %H:%M",
    "%Y年%m月%d日",
    "%Y-%m-%d",
    "%Y/%m/%d",
]


def _read_rows(path: Path, encoding: str | None) -> list[dict]:
    encodings = [encoding] if encoding else ["utf-8-sig", "gbk", "gb18030", "utf-8", "latin-1"]
    last_err = None
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                return list(csv.DictReader(f))
        except (UnicodeDecodeError, UnicodeError) as e:
            last_err = e
            continue
    raise SystemExit(f"无法读取 CSV（编码不识别）: {path}\n{last_err}")


def _field_map(header: list[str], column_map: dict) -> dict:
    """canonical -> 实际列名；大小写不敏感，找不到返回 None。"""
    lower = {str(h).strip().lower(): h for h in header}
    out = {}
    for canonical, aliases in column_map.items():
        if isinstance(aliases, str):
            aliases = [aliases]
        for alias in aliases or []:
            hit = lower.get(str(alias).strip().lower())
            if hit is not None:
                out[canonical] = hit
                break
    return out


def _parse_offset(text: str) -> timezone:
    text = str(text or "+00:00").strip()
    if text in {"Z", "z"}:
        return timezone.utc
    sign = 1
    if text.startswith("-"):
        sign = -1
        text = text[1:]
    elif text.startswith("+"):
        text = text[1:]
    try:
        hh, mm = text.split(":")[:2]
        return timezone(sign * timedelta(hours=int(hh), minutes=int(mm)))
    except (ValueError, TypeError):
        return timezone.utc


def _tz(spec):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(spec.timezone)
    except Exception:
        return _parse_offset(spec.utc_offset)


def _parse_published(value: str, spec, formats: list[str]) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    tz = _tz(spec)

    # epoch 秒/毫秒
    if raw.isdigit():
        try:
            num = int(raw)
            if len(raw) >= 13:
                num /= 1000.0
            return datetime.fromtimestamp(num, tz=tz)
        except (ValueError, OSError, OverflowError):
            pass

    iso = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        return dt.astimezone(tz)
    except ValueError:
        pass

    for fmt in formats:
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz)
            return dt.astimezone(tz)
        except ValueError:
            continue
    return None


def _source_value(row: dict, mapping: dict, spec) -> str:
    policy = spec.inputs.get("source_policy") or {"mode": "constant", "value": "real"}
    mode = str(policy.get("mode") or "constant")
    if mode == "column":
        column = policy.get("column") or "source"
        actual = mapping.get("source")
        if actual:
            return str(row.get(actual) or policy.get("default") or "real").strip()
        return str(policy.get("default") or "real")
    return str(policy.get("value") or "real")


def _make_id(row: dict, mapping: dict, post_id: str, published: str) -> str:
    parts = [
        str(row.get(mapping.get("post_id")) or post_id),
        str(row.get(mapping.get("author")) or ""),
        published,
        str(row.get(mapping.get("body")) or ""),
    ]
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"gen_{digest}"


def _limit_text(text: str, limit: int) -> str:
    return text[:limit]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="导入事件评论 CSV")
    ap.add_argument("--event", default="demo", help="Event id (default: demo)")
    ap.add_argument("--input", default=None, help="源 CSV；默认 events/<id>/input/raw_comments.csv")
    ap.add_argument("--encoding", default=None, help="源文件编码；默认自动检测")
    ap.add_argument("--dry-run", action="store_true", help="只检查，不写文件")
    ap.add_argument("--force", action="store_true", help="允许覆盖 input/comments.csv")
    ap.add_argument("--limit", type=int, default=None, help="只导入前 N 行（试点用）")
    ap.add_argument("--keep-out-of-window", action="store_true",
                    help="保留窗口外评论；默认丢弃")
    args = ap.parse_args()

    spec = load_event(args.event)
    paths = event_paths(spec)
    src = Path(args.input) if args.input else paths.input_dir / "raw_comments.csv"
    if not src.is_absolute():
        src = (Path.cwd() / src).resolve()
    if not src.exists():
        raise SystemExit(
            f"找不到源文件: {src}\n"
            f"请把评论 CSV 放在 {paths.input_dir / 'raw_comments.csv'}，"
            f"或用 --input 指定。"
        )

    rows = _read_rows(src, args.encoding)
    if not rows:
        raise SystemExit(f"源文件没有数据行: {src}")
    if args.limit:
        rows = rows[:args.limit]

    header = list(rows[0].keys())
    column_map = spec.inputs.get("column_map") or {}
    mapping = _field_map(header, column_map)

    required = {"author", "body", "published"}
    missing = [f for f in required if f not in mapping]
    if missing:
        raise SystemExit(
            f"源文件缺少必需列（按 event.json column_map 解析）: {missing}\n"
            f"实际表头: {header}\n"
            f"请补充 event.json 的 input.column_map 别名。"
        )

    formats = list(spec.inputs.get("time_formats") or DEFAULT_TIME_FORMATS)
    start = spec.start_date
    end = spec.end_date

    out_rows = []
    stats = {
        "source": str(src),
        "input_rows": len(rows),
        "accepted": 0,
        "dropped_out_of_window": 0,
        "date_parse_failed": 0,
        "missing_author": 0,
        "missing_body": 0,
        "duplicate_ids": 0,
        "generated_ids": 0,
        "orphan_parent_ids": 0,
        "unique_authors": 0,
        "missing_days": [],
    }
    seen_ids = set()
    old_to_new = {}
    pending = []

    for row in rows:
        author = str(row.get(mapping.get("author")) or "").strip()
        body = str(row.get(mapping.get("body")) or "").strip()
        published_raw = str(row.get(mapping.get("published")) or "").strip()
        if not author:
            stats["missing_author"] += 1
            continue
        if not body:
            stats["missing_body"] += 1
            continue

        dt = _parse_published(published_raw, spec, formats)
        if dt is None:
            stats["date_parse_failed"] += 1
            continue
        published = dt.isoformat()
        published_date = dt.date().isoformat()
        if not args.keep_out_of_window and start and end:
            if not (start <= published_date <= end):
                stats["dropped_out_of_window"] += 1
                continue

        post_id = str(row.get(mapping.get("post_id")) or "").strip() or "unassigned"
        old_id = str(row.get(mapping.get("comment_id")) or "").strip()
        if not old_id:
            new_id = _make_id(row, mapping, post_id, published)
            stats["generated_ids"] += 1
        else:
            new_id = old_id
        if new_id in seen_ids:
            stats["duplicate_ids"] += 1
            continue
        seen_ids.add(new_id)
        if old_id:
            old_to_new[old_id] = new_id

        pending.append({
            "old_id": old_id,
            "new_id": new_id,
            "row": row,
            "published": published,
            "published_date": published_date,
            "post_id": post_id,
            "author": author,
            "body": body,
        })

    for item in pending:
        row = item["row"]
        parent_old = str(row.get(mapping.get("parent_id")) or "").strip() if mapping.get("parent_id") else ""
        parent_new = old_to_new.get(parent_old, parent_old) if parent_old else ""
        if parent_old and parent_old not in old_to_new and parent_old not in seen_ids:
            stats["orphan_parent_ids"] += 1
        channel = ""
        if mapping.get("channel"):
            channel = str(row.get(mapping["channel"]) or "").strip()
        depth = str(row.get(mapping["depth"]) or "").strip() if mapping.get("depth") else ""
        likes = str(row.get(mapping["likes"]) or "").strip() if mapping.get("likes") else ""
        post_title = (str(row.get(mapping["post_title"]) or "").strip()
                      if mapping.get("post_title") else "")
        out_rows.append({
            "comment_id": item["new_id"],
            "author": item["author"],
            "body": _limit_text(item["body"], 20000),
            "published": item["published"],
            "published_date": item["published_date"],
            "post_id": item["post_id"],
            "post_title": post_title,
            "parent_id": parent_new,
            "depth": depth,
            "channel": channel or spec.platform,
            "source": _source_value(row, mapping, spec),
            "likes": likes,
            "source_file": src.name,
        })

    stats["accepted"] = len(out_rows)
    stats["unique_authors"] = len({r["author"] for r in out_rows})
    present_days = {r["published_date"] for r in out_rows}
    from datetime import date
    d = date.fromisoformat(start)
    last = date.fromisoformat(end)
    missing_days = []
    while d <= last:
        if d.isoformat() not in present_days:
            missing_days.append(d.isoformat())
        d += timedelta(days=1)
    stats["missing_days"] = missing_days

    print(f"事件: {spec.event_id}")
    print(f"源文件: {src}")
    print(f"输入 {stats['input_rows']} 行，接受 {stats['accepted']} 行")
    print(f"  窗口外丢弃: {stats['dropped_out_of_window']}")
    print(f"  日期解析失败: {stats['date_parse_failed']}")
    print(f"  缺作者/正文: {stats['missing_author']} / {stats['missing_body']}")
    print(f"  重复 ID: {stats['duplicate_ids']}，生成 ID: {stats['generated_ids']}")
    print(f"  孤儿父评论: {stats['orphan_parent_ids']}")
    print(f"  唯一作者: {stats['unique_authors']}")
    if missing_days:
        print(f"  窗口内缺评论的日期: {len(missing_days)} 天（前 5: {missing_days[:5]}）")

    if args.dry_run:
        print("\n[dry-run] 未写入")
        return

    out_path = paths.input_comments
    if out_path.exists() and not args.force:
        raise SystemExit(f"输出已存在，拒绝覆盖: {out_path}\n加 --force 允许覆盖。")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CANONICAL_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(out_rows)

    report_path = paths.import_report_json
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps({"event_id": spec.event_id, **stats}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n已写入 {out_path}（{len(out_rows)} 行）")
    print(f"已写入 {report_path}")


if __name__ == "__main__":
    main()
