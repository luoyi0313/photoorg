"""报告：report.md（人读，带状态/来源标记）与 report.csv（机读明细）。

每人每张照片都印来源标记：auto (0.87) / review (0.52) / manual。
"""
from __future__ import annotations

import csv
import io

from .results import BatchView
from .state import PUBLISHED
from .util import parse_batch_name


def _cell(x) -> str:
    return str(x).replace("|", "\\|").replace("\n", " ")


def build_csv(view: BatchView) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["photo", "person", "status", "score", "source", "reason"])
    for key in sorted(view.pairs):
        pr = view.pairs[key]
        score = "" if pr.score < 0 else f"{pr.score:.4f}"
        w.writerow(
            [
                pr.photo_name,
                pr.person_name,
                pr.status,
                score,
                "manual" if pr.manual else pr.auto_status,
                _cell(pr.reason),
            ]
        )
    return buf.getvalue()


def build_markdown(view: BatchView) -> str:
    date, activity = parse_batch_name(view.batch)
    st = view.state
    published = st.get("state") == PUBLISHED
    pub = st.get("published") or {}
    stats = view.stats

    L: list[str] = []
    L.append(f"# 出勤报告 · {view.batch}")
    L.append("")
    if date:
        L.append(f"- 日期：{date}　活动：{activity}")
    L.append(f"- 班级：{view.class_name}")
    L.append(
        f"- 状态：**{st.get('label', 'DRAFT')}**"
        + (f"（确认人 {pub.get('by', '')} · {pub.get('at', '')[:16].replace('T', ' ')}）" if published else "")
    )
    if not published:
        L.append("")
        L.append(f"> ⚠️ **{view.draft_banner}**")
    L.append("")

    # ---------------- 概览
    L.append("## 一、概览")
    L.append("")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append(f"| 照片 | {stats.get('n_photos', 0)} 张（读取失败 {stats.get('n_error', 0)} 张）|")
    L.append(f"| 检出人脸 | {stats.get('n_faces', 0)} 张（质量达标 {stats.get('n_usable_faces', 0)} 张）|")
    L.append(f"| 判定为出现 | {stats.get('n_pairs_present', 0)} 条「照片×人」|")
    L.append(f"| 灰区待确认 | {stats.get('n_review', 0)} 条 |")
    L.append(f"| 人工修正 | {stats.get('n_manual', 0)} 条 |")
    L.append(f"| 名单人数 | {len(view.persons)} 人 |")
    L.append("")
    if stats.get("incomplete"):
        L.append(
            f"> ⚠️ **批次质量不达标**：{stats.get('incomplete_reason', '')}。"
            "本期不出「未出现」结论——宁可说不确定，不要给错名单。"
        )
        L.append("")

    # ---------------- 出勤
    L.append("## 二、出勤明细")
    L.append("")
    L.append("| 姓名 | 张数 | 来源 | 状态 |")
    L.append("|---|---|---|---|")
    for p in sorted(view.persons, key=lambda r: (r.absent, -r.present_count, r.name)):
        if p.absent:
            flag = "未出现"
            if stats.get("incomplete"):
                flag = "未出现（批次质量不达标，仅供参考）"
            elif p.absent_confirmed:
                flag = "未出现（人工已确认）"
            else:
                flag = "未出现（待确认）"
            L.append(f"| {_cell(p.name)} | 0 | — | **{flag}** |")
        else:
            src = f"{p.auto_count} auto"
            if p.manual_count:
                src += f" · {p.manual_count} manual"
            L.append(f"| {_cell(p.name)} | {p.present_count} | {src} | 出现 |")
    L.append("")

    # ---------------- 未出现 + 反证
    absent = [p for p in view.persons if p.absent]
    L.append("## 三、未出现名单与反证")
    L.append("")
    if stats.get("incomplete"):
        L.append("批次质量不达标，本期不输出「未出现」结论。")
    elif not absent:
        L.append("名单上所有人都出现了。")
    else:
        L.append("「未出现」旁边附该人本批次最像的几张脸——一眼看出是真没来，还是检测/阈值出问题。")
        L.append("")
        L.append("| 姓名 | 确认 | 最像的 3 张 |")
        L.append("|---|---|---|")
        for p in absent:
            ev = "；".join(
                f"{e['photo_name']} ({e['score']:.2f})" for e in p.evidence
            ) or "本批次没有任何相近的人脸"
            conf = "✅ " + (view.state.get("confirmations", {}).get(str(p.person_id), {}).get("at", "")[:16].replace("T", " ")
                            if p.absent_confirmed else "❌ 待确认")
            L.append(f"| {_cell(p.name)} | {conf} | {_cell(ev)} |")
    L.append("")

    # ---------------- 灰区
    L.append("## 四、灰区待确认")
    L.append("")
    if not view.review_items:
        L.append("没有灰区，说明这次数据很干净。")
    else:
        L.append(f"共 {len(view.review_items)} 条。跑 `photoorg review` 打开复核页，键盘 1/2/3 选人，X 不认识，2~3 分钟过完。")
        L.append("")
        L.append("| 照片 | 候选 | 相似度 | 质量 | 备注 |")
        L.append("|---|---|---|---|---|")
        for r in view.review_items[:200]:
            cand = " / ".join(f"{c['name']} {c['score']:.2f}" for c in r["candidates"][:3])
            L.append(
                f"| {_cell(r['photo_name'])} | {_cell(cand)} | {r['score']:.2f} | "
                f"{r['quality']:.2f} | {_cell(r['reasons'])} |"
            )
        if len(view.review_items) > 200:
            L.append(f"| … | 还有 {len(view.review_items) - 200} 条，见 report.csv / UI | | | |")
    L.append("")

    # ---------------- 无法识别
    L.append("## 五、名单外的人脸")
    L.append("")
    if not view.unknown_faces:
        L.append("没有不可信/名单外的人脸。")
    else:
        L.append(
            f"共 {len(view.unknown_faces)} 张脸不认识（可能是没录名单的人、路过的家长，也可能是照片里的照片）。"
        )
        L.append("")
        L.append("| 照片 | 最像的人 | 相似度 |")
        L.append("|---|---|---|")
        for r in view.unknown_faces[:50]:
            top = r["candidates"][0] if r["candidates"] else None
            cand = f"{top['name']} {top['score']:.2f}" if top else "—"
            L.append(f"| {_cell(r['photo_name'])} | {_cell(cand)} | {r['score']:.2f} |")
        if len(view.unknown_faces) > 50:
            L.append(f"| … | 还有 {len(view.unknown_faces) - 50} 条 | |")
    L.append("")

    # ---------------- 质量
    L.append("## 六、批次质量指标")
    L.append("")
    L.append("| 指标 | 值 |")
    L.append("|---|---|")
    L.append(f"| 平均每张照片人脸数 | {stats.get('avg_faces_per_photo', 0)} |")
    L.append(f"| 检测失败率 | {stats.get('fail_ratio', 0):.0%} |")
    L.append(f"| 质量不达标率 | {stats.get('unusable_ratio', 0):.0%} |")
    L.append(f"| 最大脸宽 | {stats.get('max_face_w', 0)} px |")
    L.append("")

    # ---------------- 人工变更摘要
    L.append("## 七、人工修改摘要")
    L.append("")
    manual = [pr for pr in view.pairs.values() if pr.manual]
    if not manual:
        L.append("本批次没有人工修改。")
    else:
        L.append(f"共 {len(manual)} 条：")
        L.append("")
        L.append("| 照片 | 人 | 结论 | 原因 |")
        L.append("|---|---|---|---|")
        for pr in sorted(manual, key=lambda x: (x.photo_name, x.person_name))[:200]:
            L.append(
                f"| {_cell(pr.photo_name)} | {_cell(pr.person_name)} | "
                f"{'出现' if pr.status == 'present' else '不出现'} | {_cell(pr.reason)} |"
            )
    L.append("")
    L.append("---")
    L.append("")
    L.append("> 本报告由本机程序生成，照片未离开这台电脑。")
    L.append("> `out/` 是派生产物，每次运行自动重建；改结论请改 overrides（界面 / CSV），不要直接改文件。")
    return "\n".join(L) + "\n"
