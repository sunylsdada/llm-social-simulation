"""
事件包（event package）解析与配置构建。

一个事件包是一个自包含目录：

    events/<event_id>/
        event.json
        input/comments.csv
        input/stimuli.csv
        interim/
        processed/
        outputs/runs/<run_id>/

本模块只负责“读配置、解析路径、构建每次运行的 CONFIG、做基础校验”；
不负责评论导入、LLM 打分或仿真本身。

设计原则：
- 不修改模块级 CONFIG；每次运行都 deepcopy 后注入事件配置。
- The framework is event-package based; there is no hidden default dataset.
- 事件目录外的路径一律拒绝，避免 ../ 路径穿越。
"""

from __future__ import annotations

import copy
import csv
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from simulation.infra.config import CONFIG, PROJECT_ROOT

EVENTS_DIR = PROJECT_ROOT / "events"
DEFAULT_EVENT_ID = "demo"
EVENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
REQUIRED_TOP_LEVEL = ("event_id", "name", "window", "phases")
STIMULUS_TYPES = {"fact", "gov_comm", "new_topic"}


class EventError(ValueError):
    """事件配置或事件数据不合法。"""


@dataclass(frozen=True)
class Issue:
    level: str          # 'error' | 'warning' | 'info'
    message: str
    where: str = ""

    def __str__(self) -> str:
        loc = f" [{self.where}]" if self.where else ""
        return f"{self.level.upper()}{loc}: {self.message}"


@dataclass(frozen=True)
class EventSpec:
    """一个事件包的只读描述。"""

    event_id: str
    root: Path
    spec: dict

    @property
    def name(self) -> str:
        return str(self.spec.get("name") or self.event_id)

    @property
    def language(self) -> str:
        return str(self.spec.get("language") or "zh")

    @property
    def platform(self) -> str:
        return str(self.spec.get("platform") or "unknown")

    @property
    def timezone(self) -> str:
        return str(self.spec.get("timezone") or "Asia/Shanghai")

    @property
    def utc_offset(self) -> str:
        return str(self.spec.get("utc_offset") or "+08:00")

    @property
    def window(self) -> dict:
        return dict(self.spec.get("window") or {})

    @property
    def start_date(self) -> str:
        return str(self.window.get("start") or "")

    @property
    def end_date(self) -> str:
        return str(self.window.get("end") or "")

    @property
    def total_days(self) -> int:
        w = self.window
        if w.get("total_days"):
            return int(w["total_days"])
        from datetime import date
        start = date.fromisoformat(self.start_date)
        end = date.fromisoformat(self.end_date)
        return (end - start).days + 1

    @property
    def phases(self) -> list[dict]:
        return list(self.spec.get("phases") or [])

    @property
    def simulation(self) -> dict:
        return dict(self.spec.get("simulation") or {})

    @property
    def inputs(self) -> dict:
        return dict(self.spec.get("input") or {})

    @property
    def prompts(self) -> dict:
        return dict(self.spec.get("prompts") or {})

    @property
    def scoring(self) -> dict:
        return dict(self.spec.get("scoring") or {})

    @property
    def keywords(self) -> dict:
        return dict(self.spec.get("keywords") or {})

    @property
    def demographics(self) -> dict:
        return dict(self.spec.get("demographics") or {})

    @property
    def validation(self) -> dict:
        return dict(self.spec.get("validation") or {})

    @property
    def seats(self) -> int:
        return int(self.simulation.get("seats") or 40)

    @property
    def decision_days(self) -> list[int]:
        return [int(x) for x in (self.simulation.get("decision_days") or [])]

    @property
    def mode_default(self) -> str:
        mode = str(self.simulation.get("mode_default") or "replay")
        return mode if mode in {"replay", "counterfactual"} else "replay"

    @property
    def intervention_timing(self) -> str:
        timing = str(self.simulation.get("intervention_timing") or "split")
        return timing if timing in {"split", "post"} else "split"

    @property
    def strict_comment_targets(self) -> bool:
        return bool(self.simulation.get("strict_comment_targets", False))

    @property
    def allow_fewer_seats(self) -> bool:
        return bool(self.simulation.get("allow_fewer_seats", False))


@dataclass(frozen=True)
class EventPaths:
    """事件包内的固定路径。"""

    root: Path
    input_dir: Path
    input_comments: Path
    input_stimuli: Path
    source_dir: Path
    interim_dir: Path
    processed_dir: Path
    outputs_dir: Path
    runs_dir: Path

    user_csv: Path
    initial_states_csv: Path
    post_csv: Path
    comments_clean: Path
    scores_cache: Path
    author_big5_cache: Path
    initial_states_cache: Path
    negative_daily_csv: Path
    negative_daily_json: Path
    phase_sentiment_json: Path
    utility_matrix_json: Path
    import_report_json: Path

    def run_dir(self, run_id: str) -> Path:
        safe = _safe_component(run_id)
        return self.runs_dir / safe

    def record_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "record.json"

    def log_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "simulation.log"


def _safe_component(value: str) -> str:
    text = str(value or "").strip()
    if not text or text in {".", ".."} or "/" in text or "\\" in text:
        raise EventError(f"非法路径片段: {value!r}")
    if not re.match(r"^[A-Za-z0-9._-]+$", text):
        raise EventError(f"路径片段只能包含字母/数字/._-: {value!r}")
    return text


def _resolve_inside(root: Path, rel: str) -> Path:
    """把事件配置里的相对路径解析到事件根内，拒绝路径穿越。"""
    if not rel:
        raise EventError("事件配置里的路径不能为空")
    p = Path(rel)
    if p.is_absolute():
        raise EventError(f"事件配置里的路径必须是相对路径: {rel}")
    resolved = (root / p).resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise EventError(f"路径穿越，拒绝访问事件根之外: {rel}")
    return resolved


def default_events_root() -> Path:
    """事件根目录；测试/多数据集可用 YUQING_EVENTS_DIR 覆盖。"""
    return Path(os.environ.get("YUQING_EVENTS_DIR") or EVENTS_DIR)


def list_events(events_root: Path | None = None) -> list[str]:
    root = Path(events_root) if events_root else default_events_root()
    if not root.exists():
        return []
    return sorted(
        p.name for p in root.iterdir()
        if p.is_dir() and not p.name.startswith("_") and (p / "event.json").exists()
    )


def load_event(event_id: str | None = None, events_root: Path | None = None) -> EventSpec:
    """加载事件描述。event_id=None 时使用默认事件。"""
    root = Path(events_root) if events_root else default_events_root()
    event_id = str(event_id or DEFAULT_EVENT_ID)
    if not EVENT_ID_RE.match(event_id):
        raise EventError(f"非法 event_id: {event_id!r}")

    event_root = root / event_id
    spec_path = event_root / "event.json"
    if not spec_path.exists():
        raise EventError(
            f"找不到事件配置: {spec_path}\n"
            f"可用事件: {list_events(root) or '（无可事件）'}\n"
            f"新事件请先复制 events/_template 并填写 event.json。"
        )

    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise EventError(f"event.json 不是合法 JSON: {e}") from e

    if not isinstance(spec, dict):
        raise EventError("event.json 顶层必须是对象")

    spec.setdefault("event_id", event_id)
    if spec["event_id"] != event_id:
        raise EventError(
            f"event.json 的 event_id={spec['event_id']!r} 与目录名 {event_id!r} 不一致"
        )

    missing = [k for k in REQUIRED_TOP_LEVEL if k not in spec]
    if missing:
        raise EventError(f"event.json 缺少必填字段: {missing}")

    return EventSpec(event_id=event_id, root=event_root, spec=spec)


def event_paths(spec: EventSpec) -> EventPaths:
    inputs = spec.inputs
    root = spec.root
    input_dir = _resolve_inside(root, "input")
    interim = _resolve_inside(root, "interim")
    processed = _resolve_inside(root, "processed")
    outputs = _resolve_inside(root, "outputs")

    comments = _resolve_inside(root, inputs.get("comments") or "input/comments.csv")
    stimuli = _resolve_inside(root, inputs.get("stimuli") or "input/stimuli.csv")

    return EventPaths(
        root=root,
        input_dir=input_dir,
        input_comments=comments,
        input_stimuli=stimuli,
        source_dir=_resolve_inside(root, "source"),
        interim_dir=interim,
        processed_dir=processed,
        outputs_dir=outputs,
        runs_dir=outputs / "runs",
        user_csv=processed / "user.csv",
        initial_states_csv=processed / "initial_states.csv",
        post_csv=processed / "post.csv",
        comments_clean=interim / "comments_clean.csv",
        scores_cache=interim / "comment_scores.jsonl",
        author_big5_cache=interim / "author_big5.jsonl",
        initial_states_cache=interim / "initial_states_v2.jsonl",
        negative_daily_csv=processed / "negative_emotion_daily.csv",
        negative_daily_json=processed / "negative_emotion_daily.json",
        phase_sentiment_json=processed / "phase_sentiment.json",
        utility_matrix_json=processed / "utility_matrix.json",
        import_report_json=interim / "import_report.json",
    )


def phase_cutoffs(spec: EventSpec) -> list[tuple[str, str]]:
    """返回 [(phase_id, 该阶段最后一天的日期), ...]，用于按日期归属评论。"""
    from datetime import date, timedelta
    start = date.fromisoformat(spec.start_date)
    out = []
    for ph in sorted(spec.phases, key=lambda p: int(p.get("start_day", 0))):
        end_day = int(ph["end_day"])
        out.append((str(ph["id"]), (start + timedelta(days=end_day - 1)).isoformat()))
    return out


def phase_names(spec: EventSpec) -> dict[str, str]:
    return {str(ph["id"]): str(ph.get("name") or ph["id"]) for ph in spec.phases}


def event_context(spec: EventSpec) -> dict:
    """给 prompt / 机制使用的上下文字典。"""
    p = spec.prompts
    return {
        "event_id": spec.event_id,
        "event_name": p.get("event_name") or spec.name,
        "event_description": p.get("event_description") or "",
        "location": p.get("location") or "",
        "official_body": p.get("official_body") or "负责处置此事的官方机构",
        "topic_shift_guidance": p.get("topic_shift_guidance")
        or "科技创新、民生改善、文化活动、体育赛事等积极方向",
        "fallback_topic": p.get("fallback_topic") or {
            "title": "本地积极进展",
            "content": "近日本地在民生与公共服务方面取得积极进展。",
        },
        "source_labels": p.get("source_labels") or {
            "official": "政府官方发布",
            "local_news": "本地新闻",
            "government_notice_title": "关于事件的官方通报",
        },
        "language": spec.language,
        "platform": spec.platform,
        "timezone": spec.timezone,
        "start_date": spec.start_date,
        "end_date": spec.end_date,
        "total_days": spec.total_days,
        "scoring_fields": dict(spec.scoring.get("fields") or {}),
        "archetypes": spec.scoring.get("archetypes"),
    }


def _apply_overrides(cfg: dict, overrides: dict | None) -> None:
    """Apply explicit CLI overrides; unknown keys are an error."""
    if not overrides:
        return
    sc = cfg["simulation_config"]
    llm = cfg.setdefault("LLM_config", {})
    stimulus = cfg.setdefault("stimulus_config", {})

    for key, value in overrides.items():
        if value is None:
            continue
        if key == "seed":
            sc["seed"] = int(value)
        elif key == "total_days":
            sc["total_days"] = int(value)
        elif key == "government_decision_days":
            sc["government_decision_days"] = [int(x) for x in value]
        elif key == "max_agents":
            sc["max_agents"] = int(value)
        elif key == "agent_uids":
            sc["agent_uids"] = [int(x) for x in value] or None
        elif key == "max_workers":
            sc["max_workers"] = int(value)
        elif key == "stimulus_mode":
            stimulus["mode"] = str(value)
        elif key == "model_name":
            llm["model_name"] = str(value)
        elif key == "llm_type":
            llm["llm_type"] = str(value)
        elif key == "record":
            cfg["data_path"]["record"] = str(value)
        elif key == "log":
            cfg["data_path"]["log"] = str(value)
        elif key == "api_key":
            llm["api_key"] = str(value)
        elif key == "base_url":
            llm["base_url"] = str(value)
        else:
            raise EventError(f"unknown config override: {key}")
        cfg.setdefault("_overrides_applied", []).append(key)


def _load_utility_matrix(spec: EventSpec) -> dict | None:
    path = event_paths(spec).utility_matrix_json
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise EventError(f"invalid utility matrix JSON: {path}: {e}") from e
    matrix = data.get("utility_matrix") if isinstance(data, dict) else None
    if not isinstance(matrix, dict):
        raise EventError(f"invalid utility matrix format: {path}")
    return matrix


def build_config(
    event_id: str | None = None,
    run_id: str = "baseline",
    overrides: dict | None = None,
    events_root: Path | None = None,
) -> dict:
    """Build a per-run config from an event package (deep-copied)."""
    spec = load_event(event_id, events_root=events_root)
    cfg = copy.deepcopy(CONFIG)
    paths = event_paths(spec)
    sim = spec.simulation

    cfg["event_id"] = spec.event_id
    cfg["event_name"] = spec.name
    cfg["event_context"] = event_context(spec)
    cfg["run_id"] = _safe_component(run_id)

    run_dir = paths.run_dir(run_id)
    cfg.setdefault("data_path", {}).update({
        "user": str(paths.user_csv),
        "initial_states": str(paths.initial_states_csv),
        "posts": str(paths.post_csv),
        "record": str(run_dir / "record.json"),
        "log": str(run_dir / "simulation.log"),
        "load_checkpoint": str(run_dir / "checkpoint" / "check.pickle"),
        "save_checkpoint": str(run_dir / "checkpoint" / "check.pickle"),
    })

    sc = cfg["simulation_config"]
    sc["total_days"] = spec.total_days
    sc["government_decision_days"] = list(spec.decision_days)
    sc["max_agents"] = max(1, int(sim.get("max_agents") or spec.seats))
    if sim.get("max_workers"):
        sc["max_workers"] = int(sim["max_workers"])

    cfg.setdefault("stimulus_config", {})["mode"] = spec.mode_default

    agent_cfg = cfg.setdefault("agent_config", {})
    for key in (
        "affect_reversion", "initial_noise", "max_posts_per_agent",
        "max_comments_per_post", "freshness_max_age_days",
        "max_pinned_old_notices",
    ):
        if key in sim:
            agent_cfg[key] = sim[key]

    cfg["panic_keywords"] = list(spec.keywords.get("panic") or [])
    cfg["negative_emotion_words"] = list(spec.keywords.get("negative") or [])
    cfg.setdefault("strategy_config", {})["suppress_keywords"] = list(
        spec.keywords.get("suppress") or []
    )

    matrix = _load_utility_matrix(spec)
    if matrix:
        cfg["strategy_config"]["utility_matrix"] = matrix

    cfg["intervention_timing"] = spec.intervention_timing
    cfg["strict_comment_targets"] = spec.strict_comment_targets

    _apply_overrides(cfg, overrides)
    return cfg


def sanitize_config(cfg: dict) -> dict:
    """返回可安全写入 record 的配置副本：移除所有 api_key 字段。"""
    def walk(value: Any) -> Any:
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                if str(k).lower() in {"api_key", "apikey", "secret", "token"}:
                    out[k] = "<redacted>"
                else:
                    out[k] = walk(v)
            return out
        if isinstance(value, list):
            return [walk(v) for v in value]
        return value

    return walk(copy.deepcopy(cfg))


def _read_csv_rows(path: Path) -> list[dict]:
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030", "latin-1"):
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError:
            continue
    raise EventError(f"无法读取 CSV（编码不识别）: {path}")


def validate_event(spec: EventSpec, strict: bool = False) -> list[Issue]:
    """事件包的静态校验。返回 issues；调用方根据 error 决定是否退出。"""
    issues: list[Issue] = []

    def add(level: str, message: str, where: str = "") -> None:
        if strict and level == "warning":
            level = "error"
        issues.append(Issue(level, message, where))

    # ---- 描述字段 ----
    for key in REQUIRED_TOP_LEVEL:
        if key not in spec.spec:
            add("error", f"缺少字段 {key}", "event.json")

    if not spec.start_date or not spec.end_date:
        add("error", "window.start / window.end 不能为空", "event.json")
    else:
        try:
            from datetime import date
            start = date.fromisoformat(spec.start_date)
            end = date.fromisoformat(spec.end_date)
            if end < start:
                add("error", "window.end 早于 window.start", "event.json")
            expected = (end - start).days + 1
            if expected != spec.total_days:
                add("error", f"total_days={spec.total_days} 与日期窗口 {expected} 天不一致",
                    "event.json")
        except ValueError as e:
            add("error", f"window 日期不是 YYYY-MM-DD: {e}", "event.json")

    # ---- 阶段 ----
    phases = sorted(spec.phases, key=lambda p: int(p.get("start_day", 0)))
    if not phases:
        add("error", "phases 不能为空", "event.json")
    else:
        expected_start = 1
        seen = set()
        for ph in phases:
            pid = str(ph.get("id") or "")
            if not pid:
                add("error", "阶段缺少 id", "event.json")
            if pid in seen:
                add("error", f"阶段 id 重复: {pid}", "event.json")
            seen.add(pid)
            try:
                lo, hi = int(ph["start_day"]), int(ph["end_day"])
            except (KeyError, TypeError, ValueError):
                add("error", f"阶段 {pid} 的 start_day/end_day 不是整数", "event.json")
                continue
            if lo != expected_start:
                add("error", f"阶段 {pid} 起点 {lo}，期望 {expected_start}（必须连续）",
                    "event.json")
            if hi < lo:
                add("error", f"阶段 {pid} 的 end_day 早于 start_day", "event.json")
            expected_start = hi + 1
        if expected_start - 1 != spec.total_days:
            add("error", f"阶段覆盖到第 {expected_start - 1} 天，但 total_days={spec.total_days}",
                "event.json")

    # ---- 决策日 ----
    for day in spec.decision_days:
        if not (1 <= day <= spec.total_days):
            add("error", f"决策日 {day} 不在 1..{spec.total_days}", "event.json")
    if len(set(spec.decision_days)) != len(spec.decision_days):
        add("error", "决策日有重复", "event.json")

    # ---- 席位 ----
    if spec.seats < 1:
        add("error", f"seats 必须 >= 1，得到 {spec.seats}", "event.json")
    max_agents = int(spec.simulation.get("max_agents") or spec.seats)
    if max_agents < spec.seats:
        add("error", f"max_agents={max_agents} 小于 seats={spec.seats}", "event.json")

    # ---- 评论输入 ----
    paths = event_paths(spec)
    comments_path = paths.input_comments
    if not comments_path.exists():
        add("error", f"找不到评论输入: {comments_path.name}；请先运行 import_event", "input")
    else:
        try:
            rows = _read_csv_rows(comments_path)
        except EventError as e:
            add("error", str(e), "input")
            rows = []
        if rows:
            cols = set(rows[0].keys())
            required = {"comment_id", "author", "body", "published"}
            # 兼容旧列名
            aliases = {
                "comment_id": {"comment_id", "id"},
                "author": {"author", "user", "用户名"},
                "body": {"body", "text", "content", "评论内容"},
                "published": {"published", "time", "created_at", "发布时间"},
            }
            for logical, names in aliases.items():
                if not (cols & names):
                    add("error", f"评论输入缺少列 {logical}（可用别名 {sorted(names)}）", "input")
            ids = [str(r.get("comment_id") or r.get("id") or "") for r in rows]
            dup = len(ids) - len(set(ids))
            if dup:
                add("warning", f"comment_id 有 {dup} 个重复；导入时保留第一条", "input")
            authors = {str(r.get("author") or r.get("user") or "") for r in rows}
            authors.discard("")
            if len(authors) < spec.seats and not spec.allow_fewer_seats:
                add("error",
                    f"清洗前作者数 {len(authors)} 少于 seats={spec.seats}；"
                    f"可调小 seats 或设置 allow_fewer_seats=true", "input")
            dates = []
            for r in rows:
                raw = str(r.get("published") or r.get("time") or r.get("created_at") or "")
                dates.append(raw[:10])
            if spec.start_date and spec.end_date:
                inside = sum(1 for d in dates if spec.start_date <= d <= spec.end_date)
                if rows and inside / len(rows) < 0.8:
                    add("warning",
                        f"只有 {inside}/{len(rows)} 条评论落在窗口内；默认导入会丢弃窗口外行",
                        "input")
                days = {d for d in dates if spec.start_date <= d <= spec.end_date}
                missing = [d for d in _date_range(spec.start_date, spec.end_date) if d not in days]
                if missing:
                    add("warning",
                        f"窗口内 {len(missing)} 天没有评论（前 5 天: {missing[:5]}）", "input")

    # ---- 刺激源 ----
    stimuli_path = paths.input_stimuli
    if not stimuli_path.exists():
        if not spec.inputs.get("allow_placeholder_stimuli"):
            add("error",
                f"找不到刺激源: {stimuli_path.name}；请填写每日事实/官方表态，"
                f"或显式允许占位刺激（不可用于论文结果）", "input")
        else:
            add("warning", "刺激源缺失；将使用占位事实，结果不可用于论文", "input")
    else:
        try:
            stim_rows = _read_csv_rows(stimuli_path)
        except EventError as e:
            add("error", str(e), "input")
            stim_rows = []
        if stim_rows:
            cols = set(stim_rows[0].keys())
            required = {"day", "stimulus_type", "title", "news_content"}
            if not required <= cols:
                add("error", f"刺激源缺少列: {sorted(required - cols)}", "input")
            days = set()
            for r in stim_rows:
                try:
                    day = int(r.get("day") or 0)
                except (TypeError, ValueError):
                    add("error", f"刺激源 day 不是整数: {r.get('day')!r}", "input")
                    continue
                if not (1 <= day <= spec.total_days):
                    add("error", f"刺激源 day={day} 不在 1..{spec.total_days}", "input")
                days.add(day)
                stype = str(r.get("stimulus_type") or "fact")
                if stype not in STIMULUS_TYPES:
                    add("error", f"未知 stimulus_type: {stype}", "input")
            missing = [d for d in range(1, spec.total_days + 1) if d not in days]
            if missing:
                add("warning", f"这些天没有刺激源（前 5 天: {missing[:5]}）", "input")

    # ---- 已生成的产物（如果存在）----
    if paths.user_csv.exists():
        try:
            rows = _read_csv_rows(paths.user_csv)
            if rows and "uid" not in rows[0]:
                add("error", "user.csv 缺少 uid 列", "processed")
            if len(rows) != spec.seats:
                add("warning",
                    f"user.csv 有 {len(rows)} 行，与 seats={spec.seats} 不一致", "processed")
        except EventError as e:
            add("error", str(e), "processed")

    if paths.post_csv.exists():
        try:
            rows = _read_csv_rows(paths.post_csv)
            if rows and "day" not in rows[0]:
                add("error", "post.csv 缺少 day 列", "processed")
        except EventError as e:
            add("error", str(e), "processed")

    # ---- prompt 上下文 ----
    ctx = event_context(spec)
    for key in ("event_name", "event_description", "official_body"):
        if not ctx.get(key):
            add("error", f"prompts.{key} 不能为空", "event.json")

    return issues


def _date_range(start: str, end: str) -> list[str]:
    from datetime import date, timedelta
    d = date.fromisoformat(start)
    e = date.fromisoformat(end)
    out = []
    while d <= e:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def print_issues(issues: Iterable[Issue]) -> int:
    """打印 issues，返回 error 数量。"""
    errors = 0
    for issue in issues:
        print(issue)
        if issue.level == "error":
            errors += 1
    return errors


def main() -> None:
    import argparse
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="事件包配置检查")
    ap.add_argument("--list", action="store_true", help="列出所有事件")
    ap.add_argument("--check", metavar="EVENT_ID", default=None, help="校验指定事件")
    ap.add_argument("--strict", action="store_true", help="把 warning 当成 error")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    if args.list:
        print("\n".join(list_events()) or "（没有事件）")
        return

    if not args.check:
        ap.error("请用 --list 或 --check <event_id>")

    spec = load_event(args.check)
    issues = validate_event(spec, strict=args.strict)

    if args.json:
        print(json.dumps([
            {"level": i.level, "message": i.message, "where": i.where}
            for i in issues
        ], ensure_ascii=False, indent=2))
    else:
        print(f"事件: {spec.event_id} ({spec.name})")
        print(f"窗口: {spec.start_date} ~ {spec.end_date}，{spec.total_days} 天")
        print(f"席位: {spec.seats}，决策日: {spec.decision_days}")
        print(f"语言/平台: {spec.language}/{spec.platform}")
        if not issues:
            print("校验通过，没有发现 issue。")
        else:
            print_issues(issues)
    errors = sum(1 for i in issues if i.level == "error")
    raise SystemExit(0 if errors == 0 else 2)


if __name__ == "__main__":
    main()
