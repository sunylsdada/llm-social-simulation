"""
真实 negative 曲线 vs 仿真 panic 曲线的形态对比。

只做三件事：
  1. 把 record.json 的逐日 avg_panic 与 negative_emotion_daily.csv 的 negative
     按 day 1-15 对齐；
  2. min-max normalize each side and compute shape metrics;
     峰值日、P1-P4 阶段均值，以及单调/触顶/平坦等诊断量；
  3. 画一张双面板图并写出指标 JSON。

不调用 LLM；不调参；不做时间平移/DTW；不把形态相似写成外部效度验证。

用法:
    python experiments/compare_replay_vs_real.py \
      --record outputs/replay_small/record.json \
      --real events/<id>/processed/negative_emotion_daily.csv \
      --out-dir outputs/replay_small
"""

import argparse
import csv
import json
import math
import sys
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PHASES = []


def _abs(path):
    from simulation.infra.config import PROJECT_ROOT
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def load_real(path, min_n, window_start):
    from datetime import date as _date
    rows = []
    with open(path, 'r', encoding='utf-8-sig', newline='') as f:
        for r in csv.DictReader(f):
            date = (r.get('day') or '').strip()
            n = int(float(r.get('n') or 0))
            val = r.get('negative')
            if not date or val in (None, ''):
                continue
            if n < min_n:
                # 与 negative_emotion_daily.py 的定标口径一致：样本太少的日不参与。
                continue
            try:
                day = (_date.fromisoformat(date)
                       - _date.fromisoformat(window_start)).days + 1
            except ValueError:
                continue
            rows.append({'date': date, 'day': day, 'n': n,
                         'negative': float(val)})
    rows.sort(key=lambda x: x['day'])
    if not rows:
        raise SystemExit(f'真实侧没有可用行: {path}')
    return rows


def load_sim(path):
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    curve = (data.get('indicators') or {}).get('panic_curve')
    if curve:
        pairs = [(int(x['day']), float(x['avg_panic'])) for x in curve]
    else:
        pairs = [(int(x['day']), float(x['avg_panic']))
                 for x in data.get('daily_snapshots', [])]
    if not pairs:
        raise SystemExit(f'record 里没有 panic 曲线: {path}')
    return data, dict(pairs)


def minmax(values):
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        return [0.5] * len(values), True
    return [(v - lo) / (hi - lo) for v in values], False


def _ranks(values):
    """平均秩，处理并列。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def pearson(x, y):
    if len(x) != len(y) or len(x) < 2:
        return None
    mx = sum(x) / len(x)
    my = sum(y) / len(y)
    sx = sum((v - mx) ** 2 for v in x)
    sy = sum((v - my) ** 2 for v in y)
    if sx < 1e-15 or sy < 1e-15:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / math.sqrt(sx * sy)


def spearman(x, y):
    return pearson(_ranks(x), _ranks(y))


def rmse(x, y):
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(x, y)) / len(x))


def diff(values):
    return [values[i + 1] - values[i] for i in range(len(values) - 1)]


def phase_means(days, values):
    out = {}
    for name, lo, hi, _ in PHASES:
        vals = [v for d, v in zip(days, values) if lo <= d <= hi]
        out[name] = sum(vals) / len(vals) if vals else None
    return out


def active_band(data):
    """按天聚合活跃 agent 的 panic；只含当天真的行动了的 agent。"""
    by_day = {}
    for a in data.get('agent_actions', []):
        by_day.setdefault(int(a['day']), []).append(float(a['panic']))
    out = {}
    for day, vals in by_day.items():
        mean = sum(vals) / len(vals)
        sd = None
        if len(vals) >= 2:
            sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))
        out[day] = {'n': len(vals), 'mean': mean, 'sd': sd}
    return out


def peak_info(days, values):
    mx = max(values)
    peaks = [d for d, v in zip(days, values) if abs(v - mx) < 1e-9]
    tail = diff(values)
    tail_slope = sum(tail[-2:]) / len(tail[-2:]) if tail else 0.0
    return {
        'day': peaks[0],
        'days': peaks,
        'ambiguous': len(peaks) > 1 or tail_slope > 1e-6,
    }


def build_metrics(days, real, sim, active):
    real_norm, real_flat = minmax(real)
    sim_norm, sim_flat = minmax(sim)

    real_diff = diff(real_norm)
    sim_diff = diff(sim_norm)
    sign_total = len(real_diff)
    sign_agree = sum(
        1 for a, b in zip(real_diff, sim_diff)
        if (a > 0 and b > 0) or (a < 0 and b < 0) or (abs(a) < 1e-12 and abs(b) < 1e-12)
    )

    real_peak = peak_info(days, real)
    sim_peak = peak_info(days, sim)
    sim_diffs = diff(sim)
    sim_monotonic = (
        sum(1 for d in sim_diffs if d >= -1e-9) / len(sim_diffs)
        if sim_diffs else None
    )

    phase_real = phase_means(days, real)
    phase_sim = phase_means(days, sim)
    phase_real_norm = phase_means(days, real_norm)
    phase_sim_norm = phase_means(days, sim_norm)

    metrics = {
        'pearson_level': pearson(real, sim),
        'spearman_level': spearman(real, sim),
        'pearson_norm': pearson(real_norm, sim_norm),
        'spearman_norm': spearman(real_norm, sim_norm),
        'rmse_raw': rmse(real, sim),
        'nrmse_minmax': rmse(real_norm, sim_norm),
        'diff_pearson': pearson(real_diff, sim_diff),
        'diff_sign_agreement': sign_agree / sign_total if sign_total else None,
        'peak_day_real': real_peak['day'],
        'peak_date_real': None,
        'peak_days_real': real_peak['days'],
        'peak_day_sim': sim_peak['day'],
        'peak_date_sim': None,
        'peak_days_sim': sim_peak['days'],
        'peak_lag_days': sim_peak['day'] - real_peak['day'],
        'peak_lag_ambiguous': sim_peak['ambiguous'] or real_peak['ambiguous'],
        'phase_means_real': phase_real,
        'phase_means_sim': phase_sim,
        'phase_means_real_norm': phase_real_norm,
        'phase_means_sim_norm': phase_sim_norm,
        'phase_delta_norm': {
            k: (phase_sim_norm[k] - phase_real_norm[k])
            if phase_sim_norm[k] is not None and phase_real_norm[k] is not None else None
            for k in phase_real_norm
        },
    }

    diagnostics = {
        'sim_start': sim[0],
        'sim_end': sim[-1],
        'sim_delta': sim[-1] - sim[0],
        'sim_range': max(sim) - min(sim),
        'real_start': real[0],
        'real_end': real[-1],
        'real_delta': real[-1] - real[0],
        'real_range': max(real) - min(real),
        'level_gap_start': sim[0] - real[0],
        'level_gap_mean': sum(sim) / len(sim) - sum(real) / len(real),
        'sim_monotonic_ratio': sim_monotonic,
        'sim_days_at_ceiling': sum(1 for v in sim if v >= 0.95),
        'sim_tail_slope': (sum(sim_diffs[-2:]) / len(sim_diffs[-2:])) if sim_diffs else None,
        'sim_degenerate_normalization': sim_flat,
        'real_degenerate_normalization': real_flat,
    }

    series = []
    for i, (d, r, s) in enumerate(zip(days, real, sim)):
        a = active.get(d, {})
        series.append({
            'day': d,
            'real_negative': r,
            'real_norm': real_norm[i],
            'sim_panic': s,
            'sim_norm': sim_norm[i],
            'sim_active_n': a.get('n'),
            'sim_active_mean': a.get('mean'),
            'sim_active_sd': a.get('sd'),
        })

    return metrics, diagnostics, series


def plot(days, dates, real, sim, real_norm, sim_norm, metrics, active, out_png):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(11, 8.5), sharex=True,
        gridspec_kw={'height_ratios': [1, 1]})
    x = days

    # ---- Panel A：原始水平 ----
    ax1.plot(x, real, color='#7f7f7f', marker='o', lw=2, label='真实 negative')
    ax1.plot(x, sim, color='#1f77b4', marker='s', lw=2, label='仿真 avg_panic')

    band_x, band_mean, band_sd = [], [], []
    for d in days:
        a = active.get(d)
        if a and a.get('sd') is not None:
            band_x.append(d)
            band_mean.append(a['mean'])
            band_sd.append(a['sd'])
    if band_x:
        lo = [m - s for m, s in zip(band_mean, band_sd)]
        hi = [m + s for m, s in zip(band_mean, band_sd)]
        ax1.fill_between(band_x, lo, hi, color='#1f77b4', alpha=0.15,
                         label='当日活跃 agent 均值 ±1SD')

    ax1.set_ylim(0, 1)
    ax1.set_ylabel('取值（0–1）')
    ax1.set_title('A. 原始水平：仿真 panic 与真实 negative（构念不同，只看水平差距）')
    ax1.grid(alpha=0.3)
    ax1.legend(loc='best', fontsize=9)

    # ---- Panel B：归一化形态 ----
    ax2.plot(x, real_norm, color='#7f7f7f', marker='o', lw=2, label='真实 negative（归一化）')
    ax2.plot(x, sim_norm, color='#1f77b4', marker='s', lw=2, label='仿真 panic（归一化）')

    colors = ['#f2f2f2', '#e8f0f8', '#f2f2f2', '#e8f0f8']
    for (name, lo, hi, label), color in zip(PHASES, colors):
        ax2.axvspan(lo - 0.5, hi + 0.5, color=color, zorder=0)
        ax2.text((lo + hi) / 2, 1.20, f'{name}\n{label}',
                 ha='center', va='bottom', fontsize=8)

    def fmt(v, nd=3):
        return 'null' if v is None else f'{v:.{nd}f}'

    text = (
        f"Pearson(norm) = {fmt(metrics['pearson_norm'])}\n"
        f"Spearman(norm) = {fmt(metrics['spearman_norm'])}\n"
        f"nRMSE = {fmt(metrics['nrmse_minmax'])}\n"
        f"ΔPearson = {fmt(metrics['diff_pearson'])}\n"
        f"Δ同号率 = {fmt(metrics['diff_sign_agreement'])}\n"
        f"峰值：真实 {metrics['peak_date_real'][5:]} (day {metrics['peak_day_real']}) / "
        f"仿真 {metrics['peak_date_sim'][5:]} (day {metrics['peak_day_sim']})，"
        f"lag={metrics['peak_lag_days']:+d}"
    )
    ax2.text(0.015, 0.80, text, transform=ax2.transAxes, va='top', fontsize=8.0,
             bbox=dict(boxstyle='round', facecolor='white', alpha=0.85, edgecolor='#999999'))

    ax2.set_ylim(-0.02, 1.30)
    ax2.set_ylabel('min-max 归一化')
    ax2.set_title('B. 归一化形态对比（主指标）')
    ax2.grid(alpha=0.3)
    ax2.legend(loc='lower right', fontsize=9)

    ax2.set_xticks(days)
    ax2.set_xticklabels([d[5:] for d in dates], rotation=45, ha='right')
    ax2.set_xlabel('日期')

    fig.text(
        0.01, 0.005,
        'Note: the reference side is a comment-derived negative-emotion curve; '
        'the simulation side uses panic as a proxy. See record meta for scale/seed.\n'
        'This figure describes shape similarity only, not external validity.',
        fontsize=7.5, color='#555555', va='bottom')

    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(out_png, dpi=160)
    plt.close(fig)


def main():
    global PHASES
    parser = argparse.ArgumentParser(description='Reference vs simulated shape comparison')
    parser.add_argument('--event', default='demo', help='Event id (default: demo)')
    parser.add_argument('--run-id', default='baseline')
    parser.add_argument('--record', default=None)
    parser.add_argument('--real', default=None)
    parser.add_argument('--out-dir', default=None)
    parser.add_argument('--min-n', type=int, default=None)
    args = parser.parse_args()

    from simulation.infra.event import event_paths, load_event
    spec = load_event(args.event)
    paths = event_paths(spec)
    record_path = _abs(args.record) if args.record else paths.record_path(args.run_id)
    real_path = _abs(args.real) if args.real else paths.negative_daily_csv
    out_dir = _abs(args.out_dir) if args.out_dir else paths.outputs_dir / 'analysis'
    min_n = args.min_n or int(spec.validation.get('min_n_per_day') or 40)
    window_start = spec.start_date
    PHASES = [
        (str(p['id']), int(p['start_day']), int(p['end_day']),
         str(p.get('name') or p['id']))
        for p in sorted(spec.phases, key=lambda x: int(x['start_day']))
    ]

    out_dir.mkdir(parents=True, exist_ok=True)

    real_rows = load_real(real_path, min_n=min_n, window_start=window_start)
    data, sim_by_day = load_sim(record_path)

    days = [r['day'] for r in real_rows]
    missing = [d for d in days if d not in sim_by_day]
    if missing:
        raise SystemExit(f'record 缺少这些天: {missing}')
    real = [r['negative'] for r in real_rows]
    dates = [r['date'] for r in real_rows]
    sim = [sim_by_day[d] for d in days]

    for label, vals in (('真实', real), ('仿真', sim)):
        bad = [v for v in vals if not math.isfinite(v) or not (0.0 <= v <= 1.0)]
        if bad:
            raise SystemExit(f'{label}侧存在越界/非有限值: {bad[:5]}')

    active = active_band(data)
    metrics, diagnostics, series = build_metrics(days, real, sim, active)
    date_by_day = {r['day']: r['date'] for r in real_rows}
    from datetime import date as _date, timedelta as _td
    start_dt = _date.fromisoformat(window_start)
    metrics['peak_date_real'] = date_by_day.get(
        metrics['peak_day_real'],
        (start_dt + _td(days=metrics['peak_day_real'] - 1)).isoformat())
    metrics['peak_date_sim'] = (
        start_dt + _td(days=metrics['peak_day_sim'] - 1)).isoformat()

    meta = data.get('meta_info', {})
    config_checks = {
        'stimulus_mode': meta.get('stimulus_mode'),
        'government_decision_days': meta.get('government_decision_days'),
        'user_count': meta.get('user_count'),
        'seed': meta.get('seed'),
        'n_days': len(days),
        'government_trajectory_len': len(data.get('government_trajectory', [])),
        'agent_uids': meta.get('agent_uids'),
        'initial_avg_panic': meta.get('initial_avg_panic'),
    }

    warnings = []
    if config_checks['stimulus_mode'] != 'replay':
        warnings.append('stimulus_mode 不是 replay（对比历史重演时建议用 replay）')
    if config_checks['government_decision_days']:
        warnings.append(
            f'政府决策日非空: {config_checks["government_decision_days"]}；'
            f'这不是纯历史重演')
    if config_checks['government_trajectory_len'] != 0:
        warnings.append('政府决策轨迹非空')

    real_norm, _ = minmax(real)
    sim_norm, _ = minmax(sim)
    plot(days, dates, real, sim, real_norm, sim_norm, metrics, active,
         out_dir / 'replay_vs_real.png')

    result = {
        'inputs': {
            'record': str(record_path),
            'real_csv': str(real_path),
        },
        'config_checks': config_checks,
        'warnings': warnings,
        'series': series,
        'metrics': metrics,
        'diagnostics': diagnostics,
        'caveats': [
            '真实侧 negative 来自评论语料（可能是真实+合成混合），不是天然的真实世界测量。',
            '仿真侧 panic 与真实 negative 不是同一构念，原始水平不可直接比较。',
            f'本次只有 {config_checks.get("user_count")} 个 agent、{len(days)} 个日点；'
            '不估计抽样不确定性，不做显著性检验。',
            f'归一化使用各自 {len(days)} 个可用日的 min-max；'
            '平坦序列的归一化值按 0.5 约定，nRMSE 仅作粗略参考。',
            '不做时间平移/滞后搜索；峰值滞后仅在非平台时才有明确含义。',
            '不构成外部效度验证。',
        ],
    }

    out_json = out_dir / 'replay_vs_real.json'
    with open(out_json, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)

    print('\n===== 形态对比 =====')
    print(f'record: {record_path}')
    print(f'真实:   {real_path}')
    print(f'天数:   {len(days)}')
    if warnings:
        print('警告: ' + '；'.join(warnings))
    print(f"Pearson(norm)={metrics['pearson_norm']}, "
          f"Spearman(norm)={metrics['spearman_norm']}, "
          f"nRMSE={metrics['nrmse_minmax']:.3f}")
    print(f"ΔPearson={metrics['diff_pearson']}, "
          f"Δ同号率={metrics['diff_sign_agreement']}")
    print(f"峰值日: 真实 {metrics['peak_date_real']} (day {metrics['peak_day_real']}), "
          f"仿真 {metrics['peak_date_sim']} (day {metrics['peak_day_sim']}), "
          f"lag={metrics['peak_lag_days']:+d}")
    print(f"阶段均值 real={metrics['phase_means_real']}")
    print(f"阶段均值 sim ={metrics['phase_means_sim']}")
    print(f"诊断: monotonic={diagnostics['sim_monotonic_ratio']}, "
          f"ceiling_days={diagnostics['sim_days_at_ceiling']}, "
          f"tail_slope={diagnostics['sim_tail_slope']}, "
          f"flat_norm={diagnostics['sim_degenerate_normalization']}")
    print(f'输出: {out_json}')
    print(f'      {out_dir / "replay_vs_real.png"}')


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
    main()
