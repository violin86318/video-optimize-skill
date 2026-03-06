#!/usr/bin/env python3
"""
report_generator.py — HTML 可视化报告生成器
生成自包含 HTML 报告（内嵌 Chart.js + 视频 base64）

Usage:
    python3 report_generator.py analysis.json --video video.mp4 --archive-dir ./outputs/reports
"""

import argparse, base64, hashlib, json, os, re, sys
from datetime import datetime
from pathlib import Path


def safe_slug(title: str, max_len: int = 40) -> str:
    slug = re.sub(r'[^a-zA-Z0-9]+', '_', title).strip('_').lower()
    if not slug or not slug[0].isalpha():
        slug = "v_" + hashlib.md5(title.encode()).hexdigest()[:8]
    return slug[:max_len]


def fmt_time(seconds) -> str:
    if isinstance(seconds, str): return seconds
    s = int(seconds)
    return f"{s//60:02d}:{s%60:02d}"


def score_color(score, max_s=10):
    try: s = float(score)
    except: return "#888"
    r = s / max_s
    if r < 0.4: return "#ef4444"
    if r < 0.6: return "#f59e0b"
    if r < 0.8: return "#22c55e"
    return "#10b981"


def score_bar_html(label, score, desc="", extra=""):
    s = int(score) if score else 0
    c = score_color(s)
    pct = s * 10
    return f'''<div class="dim-card"><div class="dim-header"><span class="dim-label">{label}</span>
<span class="dim-score" style="color:{c}">{s}/10</span></div>
<div class="score-bar"><div class="score-fill" style="width:{pct}%;background:{c}"></div></div>
<p class="dim-desc">{desc}</p>{extra}</div>'''


def build_radar_data(analysis):
    dims = ["hook","narrative","pacing","visual","text_overlay","audio","cta","ending"]
    labels = ["Hook开头","叙事结构","节奏感","视觉构图","字幕设计","音乐音效","互动引导","结尾设计"]
    scores = []
    for d in dims:
        v = analysis.get(d, {})
        scores.append(v.get("score", 5) if isinstance(v, dict) else 5)
    return labels, scores


def build_screenshots_html(screenshots):
    if not screenshots: return ""
    items = ""
    for s in screenshots:
        t = s.get("time", 0)
        ts = s.get("time_str", fmt_time(t))
        b64 = s.get("base64", "")
        items += f'<div class="ss-item" onclick="seekTo({t})" title="点击跳转到 {ts}"><img src="data:image/jpeg;base64,{b64}" alt="{ts}"><span class="ss-time">{ts}</span></div>'
    return f'<div class="section"><h2>📸 截图时间线</h2><div class="ss-grid">{items}</div></div>'


def build_dimensions_html(analysis):
    html = '<div class="section"><h2>📊 8 维度详细分析</h2><div class="dim-grid">'
    configs = [
        ("hook", "🎯 Hook 开头", ["formula","template"]),
        ("narrative", "📖 叙事结构", ["type","template"]),
        ("pacing", "🥁 节奏感", ["pattern"]),
        ("visual", "🎨 视觉构图", ["color_style"]),
        ("text_overlay", "📝 字幕设计", ["style"]),
        ("audio", "🎵 音乐音效", ["voice_style","sync_evidence"]),
        ("cta", "📢 互动引导", ["cta_type","cta_time"]),
        ("ending", "🔚 结尾设计", ["ending_type"]),
    ]
    for key, label, fields in configs:
        dim = analysis.get(key, {})
        if not isinstance(dim, dict): dim = {}
        desc = dim.get("description", "暂无分析")
        extra = ""
        for f in fields:
            v = dim.get(f)
            if v: extra += f'<div class="dim-field"><b>{f}:</b> {v}</div>'
        # Timeline table for narrative
        if key == "narrative" and "timeline" in dim:
            extra += '<table class="tl-table"><tr><th>时间</th><th>章节</th><th>描述</th></tr>'
            for t in dim["timeline"]:
                extra += f'<tr><td>{t.get("start","")}-{t.get("end","")}</td><td>{t.get("label","")}</td><td>{t.get("description","")}</td></tr>'
            extra += '</table>'
        html += score_bar_html(label, dim.get("score", 0), desc, extra)
    html += '</div></div>'
    return html


def build_emotion_chart_data(analysis):
    ea = analysis.get("emotional_arc", {})
    if not isinstance(ea, dict): return [], [], [], []
    pts = ea.get("curve_points", [])
    labels, valence, arousal, annots = [], [], [], []
    for p in pts:
        labels.append(p.get("time", ""))
        valence.append(p.get("valence", 0))
        arousal.append(p.get("arousal", 5))
        if p.get("label"): annots.append({"time": p.get("time",""), "label": p.get("label","")})
    return labels, valence, arousal, annots


def build_retention_bar_html(analysis):
    rp = analysis.get("retention_prediction", {})
    if not isinstance(rp, dict): return ""
    segs = rp.get("risk_segments", [])
    if not segs: return ""
    colors = {"low": "#22c55e", "medium": "#f59e0b", "high": "#ef4444"}
    bars = ""
    for seg in segs:
        risk = seg.get("risk", "low")
        c = colors.get(risk, "#888")
        label = seg.get("label", "")
        time_range = seg.get("time", "")
        reason = seg.get("reason", "")
        fix = seg.get("fix", "")
        tip = f"{time_range} [{risk}] {reason}"
        if fix: tip += f" → {fix}"
        bars += f'<div class="ret-seg" style="background:{c}" title="{tip}"><span>{label}</span></div>'
    metrics_html = ""
    for k, label in [("hook_rate_3s","3秒留存"),("retention_30s","30秒留存"),("midpoint_retention","中点留存"),("completion_rate","完播率")]:
        v = rp.get(k)
        if v is not None: metrics_html += f'<div class="ret-metric"><span>{label}</span><b>{v:.0f}%</b></div>'
    return f'''<div class="section"><h2>📉 留存风险预测</h2>
<div class="ret-metrics">{metrics_html}</div><div class="ret-bar">{bars}</div></div>'''


def build_viral_html(analysis):
    vf = analysis.get("viral_formulas", {})
    if not isinstance(vf, dict): return ""
    html = '<div class="section"><h2>🔥 爆款公式提取</h2><div class="formula-grid">'
    sf = vf.get("script_formula", {})
    if isinstance(sf, dict):
        steps = "".join(f"<li>{s}</li>" for s in sf.get("steps", []))
        tmpl = sf.get("fill_template", "")
        html += f'<div class="formula-card"><h3>📝 脚本公式</h3><ol>{steps}</ol><div class="tmpl-box">{tmpl}</div></div>'
    ef = vf.get("emotion_formula", {})
    if isinstance(ef, dict):
        nodes = "".join(f'<span class="emo-node">{n.get("emotion","")} → {n.get("trigger","")}</span>' for n in ef.get("nodes", []))
        princ = "".join(f"<li>{p}</li>" for p in ef.get("key_principles", []))
        html += f'<div class="formula-card"><h3>💡 情绪公式</h3><div class="emo-flow">{nodes}</div><ul>{princ}</ul></div>'
    af = vf.get("algorithm_formula", {})
    if isinstance(af, dict):
        drivers = "".join(f"<li>{d}</li>" for d in af.get("drivers", []))
        tips = "".join(f"<li>{t}</li>" for t in af.get("weight_tips", []))
        html += f'<div class="formula-card"><h3>⚙️ 算法公式</h3><ul>{drivers}</ul><h4>权重提示</h4><ul>{tips}</ul></div>'
    html += '</div></div>'
    return html


def build_algorithm_html(analysis):
    af = analysis.get("algorithm_fitness", {})
    if not isinstance(af, dict): return ""
    m = af.get("metrics", {})
    pf = af.get("platform_fit", [])
    gauges = ""
    for k, label in [("completion_rate","完播率"),("interaction_rate","互动率"),("share_rate","分享率"),("save_rate","收藏率")]:
        v = m.get(k, 0)
        c = score_color(v / 10)
        gauges += f'<div class="gauge"><div class="gauge-circle" style="--pct:{v};--clr:{c}"><span>{v:.0f}%</span></div><label>{label}</label></div>'
    plats = ""
    for p in pf:
        rec = "✅ 推荐" if p.get("recommended") else "⚠️"
        plats += f'<div class="plat-card"><h4>{p.get("platform","")}</h4><div class="plat-score">{p.get("score",0)}/10</div><p>{p.get("reason","")}</p><span class="plat-rec">{rec}</span></div>'
    return f'''<div class="section"><h2>🤖 算法适配度</h2>
<div class="gauge-row">{gauges}</div><div class="plat-grid">{plats}</div></div>'''


def build_learning_html(analysis):
    lp = analysis.get("learning_path", [])
    if not lp: return ""
    cards = ""
    for item in lp[:3]:
        diff = item.get("difficulty", "入门")
        diff_cls = {"入门": "easy", "进阶": "mid", "高级": "hard"}.get(diff, "easy")
        exs = "".join(f"<li>{e}</li>" for e in item.get("exercises", []))
        cards += f'''<div class="learn-card"><div class="learn-rank">#{item.get("rank",1)}</div>
<h3>{item.get("technique","")}</h3><span class="diff-tag {diff_cls}">{diff}</span>
<p>{item.get("why","")}</p><ul>{exs}</ul>
<div class="learn-ref">参考: {item.get("reference","")}</div></div>'''
    return f'<div class="section"><h2>🎓 学习路径</h2><div class="learn-grid">{cards}</div></div>'


def build_template_html(analysis):
    rt = analysis.get("replicable_template", {})
    if not isinstance(rt, dict): return ""
    structure = rt.get("structure", "")
    shots = rt.get("shot_list", [])
    script = rt.get("script_template", "")
    shots_html = ""
    if shots:
        rows = "".join(f'<tr><td>{s.get("order","")}</td><td>{s.get("shot","")}</td><td>{s.get("duration","")}</td><td>{s.get("note","")}</td></tr>' for s in shots)
        shots_html = f'<table class="tl-table"><tr><th>#</th><th>镜头</th><th>时长</th><th>注意事项</th></tr>{rows}</table>'
    return f'''<div class="section"><h2>📋 可复制模板</h2>
<div class="tmpl-section"><h3>结构公式</h3><div class="tmpl-box">{structure}</div></div>
{shots_html}<div class="tmpl-section"><h3>文案模板</h3><pre class="tmpl-box">{script}</pre></div></div>'''


def build_top3_html(analysis):
    strengths = analysis.get("top3_strengths", [])
    improvements = analysis.get("top3_improvements", [])
    s_items = "".join(f'<li class="top-good">✅ {s}</li>' for s in strengths)
    i_items = "".join(f'<li class="top-fix">⚠️ {i}</li>' for i in improvements)
    return f'''<div class="section top3-section"><div class="top3-col">
<h2>🌟 TOP 3 亮点</h2><ul>{s_items}</ul></div>
<div class="top3-col"><h2>🔧 TOP 3 改进</h2><ul>{i_items}</ul></div></div>'''


def build_scenes_html(analysis):
    chapters = analysis.get("scene_breakdown", [])
    if not chapters: return ""
    html = '<div class="section"><h2>🎬 逐场景细拆</h2>'
    for ch in chapters:
        label = ch.get("label", "")
        html += f'<div class="chapter-block"><h3>📌 {label} [{ch.get("start","")}-{ch.get("end","")}]</h3>'
        for sc in ch.get("scenes", []):
            risk = sc.get("retention_risk", "low")
            risk_cls = {"low": "risk-low", "medium": "risk-med", "high": "risk-high"}.get(risk, "risk-low")
            techs = "".join(f'<span class="tech-tag cat-{t.get("category","").lower()}" title="{t.get("why","")}">{t.get("name","")}</span>' for t in sc.get("techniques", []))
            quote = sc.get("quote", "")
            quote_html = f'<blockquote class="scene-quote">"{quote}"</blockquote>' if quote else ""
            val = sc.get("emotion_valence", 0)
            aro = sc.get("emotion_arousal", 5)
            html += f'''<div class="scene-card {risk_cls}" data-start="{sc.get("start","")}" data-end="{sc.get("end","")}">
<div class="scene-time">{sc.get("start","")}-{sc.get("end","")}</div>
<div class="scene-body"><div class="scene-row"><b>画面:</b> {sc.get("visual","")}</div>
<div class="scene-row"><b>音频:</b> {sc.get("audio","")}</div>
<div class="scene-row"><b>情绪:</b> {sc.get("emotion","")} (效价:{val}, 唤醒:{aro})</div>
{quote_html}<div class="scene-risk"><b>留存风险:</b> <span class="{risk_cls}">{risk}</span> {sc.get("risk_reason","")}</div>'''
            if sc.get("risk_fix"): html += f'<div class="scene-fix">💡 {sc.get("risk_fix","")}</div>'
            html += f'<div class="scene-techs">{techs}</div></div></div>'
        html += '</div>'
    html += '</div>'
    return html


def build_vtt(analysis):
    chapters = analysis.get("scene_breakdown", [])
    if not chapters: return ""
    lines = ["WEBVTT", ""]
    idx = 1
    for ch in chapters:
        for sc in ch.get("scenes", []):
            start = sc.get("start", "00:00")
            end = sc.get("end", "00:30")
            # Convert mm:ss to hh:mm:ss.000
            def to_vtt_time(t):
                parts = t.split(":")
                if len(parts) == 2: return f"00:{parts[0].zfill(2)}:{parts[1].zfill(2)}.000"
                return f"{parts[0].zfill(2)}:{parts[1].zfill(2)}:{parts[2].zfill(2)}.000"
            label = f"{ch.get('label','')} | {sc.get('emotion','')}"
            lines.append(str(idx))
            lines.append(f"{to_vtt_time(start)} --> {to_vtt_time(end)}")
            lines.append(json.dumps({"chapter": ch.get("label",""), "visual": sc.get("visual",""), "emotion": sc.get("emotion",""), "risk": sc.get("retention_risk","low")}, ensure_ascii=False))
            lines.append("")
            idx += 1
    return "\n".join(lines)


def generate_html(analysis, video_path=None):
    meta = analysis.get("_meta", {})
    title = meta.get("title", "视频分析报告")
    overall = analysis.get("overall_score", "N/A")
    summary = analysis.get("summary", "")
    duration = meta.get("duration", 0)
    analyzed_at = meta.get("analyzed_at", "")

    radar_labels, radar_scores = build_radar_data(analysis)
    emo_labels, emo_valence, emo_arousal, emo_annots = build_emotion_chart_data(analysis)
    vtt_content = build_vtt(analysis)

    # Video base64
    video_b64_url = ""
    if video_path and os.path.isfile(video_path):
        with open(video_path, "rb") as f:
            vb64 = base64.b64encode(f.read()).decode("ascii")
        video_b64_url = f"data:video/mp4;base64,{vb64}"

    html = f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — 爆款视频拆解报告</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
:root{{--bg:#1a1a2e;--card:#16213e;--card2:#0f3460;--text:#e0e0e0;--text2:#a0a0b0;--accent:#e94560;--accent2:#533483;--green:#22c55e;--yellow:#f59e0b;--red:#ef4444}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,-apple-system,sans-serif;line-height:1.6}}
.container{{max-width:1200px;margin:0 auto;padding:20px}}
h1{{font-size:1.8rem;margin-bottom:8px}} h2{{font-size:1.4rem;margin-bottom:16px;color:var(--accent)}} h3{{font-size:1.1rem;margin-bottom:8px}}
.hero{{background:linear-gradient(135deg,var(--card),var(--accent2));padding:40px;border-radius:16px;margin-bottom:24px;text-align:center}}
.hero .overall{{font-size:3rem;font-weight:bold;margin:16px 0}}
.hero .summary{{color:var(--text2);max-width:700px;margin:0 auto}}
.section{{background:var(--card);border-radius:12px;padding:24px;margin-bottom:20px}}
.player-wrap{{display:flex;gap:20px;flex-wrap:wrap}}
.player-wrap video{{flex:1;min-width:300px;max-width:640px;border-radius:8px;background:#000}}
.sync-panel{{flex:1;min-width:280px;max-height:400px;overflow-y:auto;background:var(--bg);border-radius:8px;padding:12px}}
.sync-panel .active-scene{{background:var(--card2);border-left:3px solid var(--accent);padding:8px;border-radius:4px;margin-bottom:8px}}
.ss-grid{{display:flex;gap:8px;overflow-x:auto;padding:8px 0}}
.ss-item{{cursor:pointer;flex-shrink:0;position:relative;border-radius:6px;overflow:hidden;transition:transform .2s}}
.ss-item:hover{{transform:scale(1.05)}} .ss-item img{{height:80px;display:block}}
.ss-time{{position:absolute;bottom:2px;right:4px;background:rgba(0,0,0,.7);color:#fff;font-size:11px;padding:1px 4px;border-radius:3px}}
.dim-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px}}
.dim-card{{background:var(--bg);border-radius:8px;padding:16px}}
.dim-header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}}
.dim-label{{font-weight:600}} .dim-score{{font-size:1.3rem;font-weight:bold}}
.score-bar{{height:6px;background:#333;border-radius:3px;overflow:hidden;margin-bottom:8px}}
.score-fill{{height:100%;border-radius:3px;transition:width .5s}}
.dim-desc{{color:var(--text2);font-size:.9rem;margin-bottom:8px}}
.dim-field{{font-size:.85rem;color:var(--text2);margin-top:4px}}
.tl-table{{width:100%;border-collapse:collapse;margin-top:8px;font-size:.85rem}}
.tl-table th,.tl-table td{{border:1px solid #333;padding:6px 8px;text-align:left}} .tl-table th{{background:var(--card2)}}
.chart-wrap{{max-width:400px;margin:0 auto}}
.emo-chart-wrap{{max-width:700px;margin:0 auto;height:300px}}
.ret-metrics{{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:16px}}
.ret-metric{{background:var(--bg);padding:8px 16px;border-radius:8px;text-align:center}}
.ret-metric span{{display:block;font-size:.8rem;color:var(--text2)}} .ret-metric b{{font-size:1.4rem}}
.ret-bar{{display:flex;gap:2px;height:32px;border-radius:8px;overflow:hidden}}
.ret-seg{{flex:1;display:flex;align-items:center;justify-content:center;font-size:.7rem;color:#fff;cursor:pointer;min-width:60px}}
.formula-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}}
.formula-card{{background:var(--bg);border-radius:8px;padding:16px}} .formula-card h3{{color:var(--accent)}}
.formula-card ol,.formula-card ul{{padding-left:20px;margin:8px 0}}
.tmpl-box{{background:var(--card2);padding:12px;border-radius:6px;font-size:.9rem;margin-top:8px;white-space:pre-wrap}}
.emo-flow{{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}}
.emo-node{{background:var(--accent2);padding:4px 10px;border-radius:12px;font-size:.85rem}}
.gauge-row{{display:flex;gap:24px;justify-content:center;flex-wrap:wrap;margin-bottom:20px}}
.gauge{{text-align:center}} .gauge label{{display:block;margin-top:4px;font-size:.85rem;color:var(--text2)}}
.gauge-circle{{width:80px;height:80px;border-radius:50%;background:conic-gradient(var(--clr) calc(var(--pct)*3.6deg),#333 0);display:flex;align-items:center;justify-content:center;position:relative}}
.gauge-circle::before{{content:'';width:60px;height:60px;border-radius:50%;background:var(--card);position:absolute}}
.gauge-circle span{{position:relative;z-index:1;font-weight:bold;font-size:.9rem}}
.plat-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}}
.plat-card{{background:var(--bg);padding:12px;border-radius:8px}} .plat-score{{font-size:1.5rem;font-weight:bold;color:var(--accent)}}
.plat-rec{{font-size:.85rem}}
.learn-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}}
.learn-card{{background:var(--bg);padding:16px;border-radius:8px;position:relative}}
.learn-rank{{position:absolute;top:12px;right:12px;font-size:1.5rem;font-weight:bold;color:var(--accent2);opacity:.5}}
.diff-tag{{font-size:.75rem;padding:2px 8px;border-radius:10px;display:inline-block;margin:4px 0}}
.easy{{background:#22c55e33;color:#22c55e}} .mid{{background:#f59e0b33;color:#f59e0b}} .hard{{background:#ef444433;color:#ef4444}}
.learn-ref{{font-size:.8rem;color:var(--text2);margin-top:8px}}
.tmpl-section{{margin-bottom:16px}}
.top3-section{{display:flex;gap:20px;flex-wrap:wrap}}
.top3-col{{flex:1;min-width:280px}} .top3-col ul{{list-style:none;padding:0}}
.top3-col li{{padding:8px 12px;margin-bottom:8px;border-radius:6px;font-size:.95rem}}
.top-good{{background:#22c55e15;border-left:3px solid var(--green)}}
.top-fix{{background:#f59e0b15;border-left:3px solid var(--yellow)}}
.chapter-block{{margin-bottom:20px;padding:12px;background:var(--bg);border-radius:8px}}
.scene-card{{background:var(--card2);border-radius:8px;padding:12px;margin-bottom:10px;border-left:4px solid var(--green);transition:background .3s}}
.scene-card.risk-med{{border-left-color:var(--yellow)}} .scene-card.risk-high{{border-left-color:var(--red)}}
.scene-card.highlight{{background:#e9456020}}
.scene-time{{font-weight:bold;color:var(--accent);margin-bottom:6px;font-size:.9rem}}
.scene-row{{font-size:.85rem;margin-bottom:4px;color:var(--text2)}}
.scene-quote{{border-left:2px solid var(--accent2);padding:4px 8px;margin:6px 0;font-style:italic;color:var(--text2);font-size:.85rem}}
.scene-risk{{font-size:.85rem;margin-top:4px}} .risk-low{{color:var(--green)}} .risk-med{{color:var(--yellow)}} .risk-high{{color:var(--red)}}
.scene-fix{{font-size:.8rem;color:var(--yellow);margin-top:4px}}
.scene-techs{{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}}
.tech-tag{{font-size:.7rem;padding:2px 6px;border-radius:8px;background:var(--accent2);color:#fff}}
.cat-hook{{background:#e94560}} .cat-留存{{background:#f59e0b}} .cat-节奏{{background:#3b82f6}} .cat-情绪{{background:#8b5cf6}} .cat-信任{{background:#22c55e}} .cat-互动{{background:#ec4899}} .cat-视觉{{background:#06b6d4}}
@media(max-width:768px){{.player-wrap{{flex-direction:column}} .dim-grid,.formula-grid,.plat-grid,.learn-grid{{grid-template-columns:1fr}} .top3-section{{flex-direction:column}}}}
</style></head><body><div class="container">

<div class="hero">
<h1>{title}</h1>
<div class="overall" style="color:{score_color(overall)}">{overall}</div>
<div style="font-size:.9rem;color:var(--text2)">时长 {fmt_time(duration)} | 分析时间 {analyzed_at}</div>
<p class="summary">{summary}</p>
</div>

<div class="section"><h2>🕹️ 维度雷达图</h2><div class="chart-wrap"><canvas id="radarChart"></canvas></div></div>

<div class="section"><h2>🎥 视频 + 同步解读</h2>
<div class="player-wrap">
<video id="mainVideo" controls>
<source src="{video_b64_url}" type="video/mp4">
<track id="metaTrack" kind="metadata" default>
</video>
<div class="sync-panel" id="syncPanel"><p style="color:var(--text2)">播放视频查看同步解读...</p></div>
</div></div>

{build_screenshots_html(analysis.get("_screenshots", []))}
{build_dimensions_html(analysis)}

<div class="section"><h2>💓 情绪弧线</h2>
<div class="emo-desc"><b>类型:</b> {analysis.get("emotional_arc",{}).get("arc_type","")} — {analysis.get("emotional_arc",{}).get("arc_description","")}</div>
<div class="emo-chart-wrap"><canvas id="emotionChart"></canvas></div></div>

{build_retention_bar_html(analysis)}
{build_viral_html(analysis)}
{build_algorithm_html(analysis)}
{build_learning_html(analysis)}
{build_scenes_html(analysis)}
{build_template_html(analysis)}
{build_top3_html(analysis)}

</div>
<script>
// Radar Chart
new Chart(document.getElementById('radarChart'),{{type:'radar',data:{{labels:{json.dumps(radar_labels, ensure_ascii=False)},datasets:[{{label:'评分',data:{json.dumps(radar_scores)},backgroundColor:'rgba(233,69,96,0.2)',borderColor:'#e94560',pointBackgroundColor:'#e94560'}}]}},options:{{scales:{{r:{{min:0,max:10,ticks:{{stepSize:2,color:'#888'}},grid:{{color:'#333'}},pointLabels:{{color:'#e0e0e0',font:{{size:12}}}}}}}},plugins:{{legend:{{display:false}}}}}}}});

// Emotion Chart
(function(){{
const labels={json.dumps(emo_labels, ensure_ascii=False)};
const valence={json.dumps(emo_valence)};
const arousal={json.dumps(emo_arousal)};
if(labels.length>0){{
new Chart(document.getElementById('emotionChart'),{{type:'line',data:{{labels:labels,datasets:[{{label:'情绪效价',data:valence,borderColor:'#e94560',backgroundColor:'rgba(233,69,96,0.1)',yAxisID:'y',fill:true,tension:.3}},{{label:'唤醒度',data:arousal,borderColor:'#533483',backgroundColor:'rgba(83,52,131,0.1)',yAxisID:'y1',fill:true,tension:.3}}]}},options:{{responsive:true,maintainAspectRatio:false,scales:{{y:{{position:'left',min:-5,max:5,title:{{display:true,text:'效价',color:'#e94560'}},ticks:{{color:'#888'}},grid:{{color:'#222'}}}},y1:{{position:'right',min:0,max:10,title:{{display:true,text:'唤醒度',color:'#533483'}},ticks:{{color:'#888'}},grid:{{drawOnChartArea:false}}}}}},plugins:{{legend:{{labels:{{color:'#e0e0e0'}}}}}}}}}});
}}
}})();

// Video sync
(function(){{
const video=document.getElementById('mainVideo');
const panel=document.getElementById('syncPanel');
const scenes=document.querySelectorAll('.scene-card');
if(!video||!scenes.length) return;
function parseTime(t){{const p=t.split(':');return p.length===2?parseInt(p[0])*60+parseInt(p[1]):0;}}
video.addEventListener('timeupdate',function(){{
const ct=video.currentTime;
scenes.forEach(sc=>{{
const s=parseTime(sc.dataset.start||'0');
const e=parseTime(sc.dataset.end||'9999');
if(ct>=s&&ct<e){{sc.classList.add('highlight');sc.scrollIntoView({{block:'nearest',behavior:'smooth'}});}}
else{{sc.classList.remove('highlight');}}
}});
}});
}})();

function seekTo(t){{const v=document.getElementById('mainVideo');if(v){{v.currentTime=t;v.play();}}}};
</script></body></html>'''
    return html


def generate_lite(html_content, video_filename="video.mp4"):
    """生成轻量版：把 base64 data URL 替换为本地文件名"""
    return re.sub(r'data:video/mp4;base64,[A-Za-z0-9+/=]+', video_filename, html_content)


def create_archive(analysis, html_content, lite_content, video_path, archive_dir):
    """创建归档目录和 meta.json"""
    meta = analysis.get("_meta", {})
    title = meta.get("title", "untitled")
    date_str = datetime.now().strftime("%Y%m%d")
    slug = safe_slug(title)
    dir_name = f"{date_str}_{slug}"
    out_dir = os.path.join(archive_dir, dir_name)
    os.makedirs(out_dir, exist_ok=True)

    # 写入报告
    report_path = os.path.join(out_dir, "report.html")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    lite_path = os.path.join(out_dir, "report-lite.html")
    with open(lite_path, "w", encoding="utf-8") as f:
        f.write(lite_content)

    # 复制视频
    if video_path and os.path.isfile(video_path):
        import shutil
        dst = os.path.join(out_dir, "video.mp4")
        if os.path.abspath(video_path) != os.path.abspath(dst):
            shutil.copy2(video_path, dst)

    # 缩略图
    thumb_b64 = ""
    screenshots = analysis.get("_screenshots", [])
    if screenshots:
        thumb_b64 = screenshots[0].get("base64", "")[:200] + "..."

    # meta.json
    meta_data = {
        "title": title,
        "overall_score": analysis.get("overall_score"),
        "duration": meta.get("duration", 0),
        "summary": analysis.get("summary", ""),
        "analyzed_at": meta.get("analyzed_at", ""),
        "thumbnail_preview": thumb_b64,
        "dir": dir_name
    }
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta_data, f, ensure_ascii=False, indent=2)

    # 更新 index.html
    update_index(archive_dir)

    print(f"[报告] 完整报告: {report_path}")
    print(f"[报告] 轻量报告: {lite_path}")
    return report_path


def update_index(archive_dir):
    """生成/更新 index.html 汇总页"""
    entries = []
    for d in sorted(os.listdir(archive_dir), reverse=True):
        meta_path = os.path.join(archive_dir, d, "meta.json")
        if os.path.isfile(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                entries.append(json.load(f))

    cards = ""
    for e in entries:
        sc = e.get("overall_score", "?")
        cards += f'''<a class="idx-card" href="{e.get('dir','')}/report-lite.html">
<div class="idx-score" style="color:{score_color(sc)}">{sc}</div>
<div class="idx-info"><h3>{e.get('title','无标题')}</h3>
<p>{e.get('summary','')[:80]}...</p>
<span>{e.get('analyzed_at','')}</span></div></a>'''

    index_html = f'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<title>视频分析报告汇总</title><style>
body{{background:#1a1a2e;color:#e0e0e0;font-family:system-ui,sans-serif;padding:40px}}
h1{{text-align:center;margin-bottom:30px;color:#e94560}} .grid{{max-width:800px;margin:0 auto;display:flex;flex-direction:column;gap:12px}}
.idx-card{{display:flex;gap:16px;background:#16213e;padding:16px;border-radius:10px;text-decoration:none;color:#e0e0e0;transition:transform .2s}}
.idx-card:hover{{transform:translateY(-2px);background:#0f3460}}
.idx-score{{font-size:2rem;font-weight:bold;min-width:60px;display:flex;align-items:center;justify-content:center}}
.idx-info h3{{margin-bottom:4px}} .idx-info p{{color:#a0a0b0;font-size:.85rem;margin-bottom:4px}} .idx-info span{{font-size:.75rem;color:#666}}
</style></head><body><h1>📊 视频分析报告汇总</h1><div class="grid">{cards}</div></body></html>'''

    with open(os.path.join(archive_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html)


def main():
    parser = argparse.ArgumentParser(description="HTML 报告生成器")
    parser.add_argument("analysis_json", help="分析结果 JSON 文件")
    parser.add_argument("--video", "-v", default=None, help="视频文件路径")
    parser.add_argument("--archive-dir", "-a", default=None, help="归档目录")
    parser.add_argument("--output", "-o", default=None, help="输出 HTML 路径")
    args = parser.parse_args()

    with open(args.analysis_json, "r", encoding="utf-8") as f:
        analysis = json.load(f)

    html = generate_html(analysis, args.video)

    if args.archive_dir:
        lite = generate_lite(html)
        report_path = create_archive(analysis, html, lite, args.video, args.archive_dir)
        print(f"[报告] HTML 报告: {report_path}")
    elif args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"[报告] HTML 报告: {args.output}")
    else:
        out = "report.html"
        with open(out, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"[报告] HTML 报告: {out}")


if __name__ == "__main__":
    main()
