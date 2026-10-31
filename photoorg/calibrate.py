"""阈值校准（§7.2 / §8.8②）：用本班累积的人工标注算建议值 + 影响预估。

⚠️ 只给建议，绝不自动改——阈值会影响历史批次的结论，必须人拍板。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .config import AppPaths, Settings
from .db import ClassDB, OverrideStore, PersonStore
from .pipeline import UserError, load_settings
from .state import StateStore
from .util import now_iso

MIN_LABELS = 30


def _label_rows(app: AppPaths, class_name: str) -> list[tuple[float, int]]:
    """从 overrides 里挖出「硬正/硬负样本」：(当前自动分, 1=同一人 / 0=不是)。"""
    cp = app.class_paths(class_name)
    cdb = ClassDB(cp.faces_db)
    ostore = OverrideStore(cp.overrides_db)
    score_map: dict[tuple, float] = {}
    for r in cdb.pairs():
        score_map[(r["photo_hash"], int(r["person_id"]))] = float(r["score"])
    out = []
    for r in ostore.all(scope="photo_person"):
        key = (r["photo_hash"], int(r["person_id"]) if r["person_id"] else -1)
        s = score_map.get(key, 0.0)
        out.append((s, 1 if r["action"] == "present" else 0))
    return out


def _gallery_rows(app: AppPaths, class_name: str) -> tuple[list[tuple[float, int]], bool]:
    """没有足够人工标注时的兜底：用底库内部的正负样本对（乐观，但比拍脑袋强）。"""
    store = PersonStore(app.persons_db)
    klass = store.klass(class_name)
    if not klass:
        raise UserError("班级不存在")
    mat, pids, _ = store.template_matrix(int(klass["id"]))
    if mat.shape[0] < 2:
        return [], True
    sims = mat @ mat.T
    rows: list[tuple[float, int]] = []
    n = mat.shape[0]
    for i in range(n):
        for j in range(i + 1, n):
            rows.append((float(sims[i, j]), 1 if pids[i] == pids[j] else 0))
    return rows, True


def _evaluate(rows: list[tuple[float, int]], t_high: float, t_low: float) -> dict:
    if not rows:
        return {"precision": 0.0, "recall_auto": 0.0, "false_accept": 0.0,
                "miss": 0.0, "grey": 0.0, "n": 0}
    s = np.array([r[0] for r in rows], dtype=np.float32)
    y = np.array([r[1] for r in rows], dtype=np.int32)
    pred_present = s >= t_high
    tp = int(((pred_present == 1) & (y == 1)).sum())
    fp = int(((pred_present == 1) & (y == 0)).sum())
    pos = max(1, int((y == 1).sum()))
    neg = max(1, int((y == 0).sum()))
    grey = ((s >= t_low) & (s < t_high)).mean()
    return {
        "precision": tp / (tp + fp) if (tp + fp) else 1.0,
        "recall_auto": tp / pos,
        "false_accept": fp / neg,
        "miss": float(((s < t_low) & (y == 1)).sum()) / pos,
        "grey": float(grey),
        "n": len(rows),
    }


def suggest(app: AppPaths, class_name: str) -> dict:
    settings = load_settings(app, class_name)
    rows = _label_rows(app, class_name)
    basis = "人工标注"
    from_gallery = False
    if len(rows) < MIN_LABELS:
        g, ok = _gallery_rows(app, class_name)
        if not ok and not rows:
            raise UserError(
                "标注太少，底库也只有一个人，没法校准。多复核一些灰区再来。"
            )
        rows = rows + g
        basis = f"人工标注 {len(_label_rows(app, class_name))} 组 + 底库内部样本对 {len(g)} 组"
        from_gallery = True

    best = None
    for t_high in np.arange(0.35, 0.86, 0.01):
        for t_low in np.arange(0.22, t_high - 0.02, 0.01):
            m = _evaluate(rows, float(t_high), float(t_low))
            if m["false_accept"] > 0.01:      # 误归率压到 1% 以内
                continue
            if m["grey"] > 0.28:              # 灰区别失控
                continue
            score = (m["recall_auto"] - 0.35 * m["miss"]) * 100 - 12 * m["grey"]
            if best is None or score > best[0]:
                best = (score, float(t_high), float(t_low), m)
    if best is None:
        best = (0.0, settings.t_high, settings.t_low, _evaluate(rows, settings.t_high, settings.t_low))

    _, t_high, t_low, m = best
    cur = _evaluate(rows, settings.t_high, settings.t_low)
    return {
        "class": class_name,
        "basis": basis,
        "n_labels": len(rows),
        "from_gallery_only": from_gallery,
        "current": {"t_high": settings.t_high, "t_low": settings.t_low,
                    "margin": settings.margin, **cur},
        "suggested": {"t_high": round(t_high, 2), "t_low": round(t_low, 2),
                      "margin": settings.margin, **m},
        "delta": {
            "grey": round((m["grey"] - cur["grey"]) * 100, 1),
            "miss": round((m["miss"] - cur["miss"]) * 100, 1),
            "false_accept": round((m["false_accept"] - cur["false_accept"]) * 100, 1),
        },
        "advice": "阈值变化会影响历史批次结论，采纳前请想清楚；采纳后可 photoorg rebuild 重出报告。",
    }


def apply(app: AppPaths, class_name: str, t_high: float, t_low: float,
          margin: float | None = None, by: str = "ui") -> dict:
    if not (0 < t_low < t_high < 1):
        raise UserError("要求 0 < T_low < T_high < 1")
    cp = app.class_paths(class_name)
    raw = json.loads(cp.thresholds.read_text("utf-8")) if cp.thresholds.exists() else {}
    settings = Settings.from_dict(raw)
    old = {"t_high": settings.t_high, "t_low": settings.t_low, "margin": settings.margin}
    settings.t_high = float(t_high)
    settings.t_low = float(t_low)
    if margin is not None:
        settings.margin = float(margin)
    data = settings.as_dict()
    data["history"] = (raw.get("history") or []) + [
        {"at": now_iso(), "by": by, "old": old,
         "new": {"t_high": settings.t_high, "t_low": settings.t_low, "margin": settings.margin}}
    ]
    cp.ensure()
    cp.thresholds.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return {"applied": True, **data["history"][-1]}
