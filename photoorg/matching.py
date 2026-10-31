"""1:N 匹配与三区间判定（open-set，不是 closed-set argmax）。

score(person) = mean(top-2 template cosine) —— 不用质心平均（§3.3）。
中间那一层「人脸 → 人」的判定带 margin；photo×person 的结论由人脸层聚合而来。
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np

from .config import Settings


def face_uid(photo_hash: str, face_idx: int) -> str:
    return f"{photo_hash[:16]}:{face_idx}"


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))


def aggregate_person_scores(
    face_mat: np.ndarray,
    tpl_mat: np.ndarray,
    tpl_pids: np.ndarray,
    person_ids: list[int],
) -> np.ndarray:
    """(N,512) × (M,512)ᵀ → (N,len(person_ids))，每人取 top-2 模板均值。"""
    n = face_mat.shape[0]
    out = np.full((n, len(person_ids)), -1.0, dtype=np.float32)
    if n == 0 or tpl_mat.shape[0] == 0:
        return out
    sims = face_mat @ tpl_mat.T
    for j, pid in enumerate(person_ids):
        cols = np.nonzero(tpl_pids == pid)[0]
        if cols.size == 0:
            continue
        sub = sims[:, cols]
        if sub.shape[1] == 1:
            out[:, j] = sub[:, 0]
        else:
            top2 = np.partition(sub, -2, axis=1)[:, -2:]
            out[:, j] = top2.mean(axis=1)
    return out


@dataclass
class FaceDecision:
    status: str              # present | review | unknown
    person_id: int | None
    score: float
    s2: float
    margin: float
    candidates: list[dict]   # [{person_id, score}]
    reason: str = ""


def decide_face(scores: np.ndarray, person_ids: list[int], settings: Settings) -> FaceDecision:
    """单张脸的三区间判定。"""
    if scores.size == 0:
        return FaceDecision("unknown", None, 0.0, 0.0, 0.0, [], "底库为空")
    order = np.argsort(-scores)
    best = int(order[0])
    s1 = float(scores[best])
    s2 = float(scores[order[1]]) if len(order) > 1 else -1.0
    margin = s1 - s2
    cands = [
        {"person_id": int(person_ids[i]), "score": round(float(scores[i]), 4)}
        for i in order[:3]
        if scores[i] > -0.5
    ]
    if s1 < settings.t_low:
        return FaceDecision("unknown", None, s1, s2, margin, cands, "低于下限")
    if s1 >= settings.t_high and margin >= settings.margin:
        return FaceDecision("present", int(person_ids[best]), s1, s2, margin, cands, "")
    why = "灰区" if s1 < settings.t_high else "两个候选分差过小"
    return FaceDecision("review", int(person_ids[best]), s1, s2, margin, cands, why)


def build_pairs(
    photo_hash: str,
    decisions: list[tuple[int, FaceDecision]],
    scores_row: np.ndarray,
    person_ids: list[int],
    min_store: float = 0.20,
    face_idx: int = -1,
) -> list[dict]:
    """把「人脸层」的判定聚合成「照片×人」的候选结论，并保留高分但未入选的对照分。"""
    present: dict[int, float] = {}
    review: dict[int, float] = {}
    fidx_of: dict[int, int] = {}
    for fidx, d in decisions:
        if d.person_id is None:
            continue
        if d.status == "present":
            if d.score > present.get(d.person_id, -1.0):
                present[d.person_id] = d.score
                fidx_of[d.person_id] = int(fidx)
        elif d.status == "review":
            if d.score > review.get(d.person_id, -1.0):
                review[d.person_id] = d.score
                fidx_of[d.person_id] = int(fidx)

    rows: list[dict] = []
    for pid in present:
        if pid in review and review[pid] > present[pid] + 0.03:
            continue
        rows.append({"photo_hash": photo_hash, "person_id": pid, "score": present[pid],
                     "status": "present", "face_idx": fidx_of.get(pid, -1)})
    for pid in review:
        if pid in present:
            continue
        rows.append({"photo_hash": photo_hash, "person_id": pid, "score": review[pid],
                     "status": "review", "face_idx": fidx_of.get(pid, -1)})
    # 供「未出现反证」使用的对照分（只在分数不低时才存，避免库膨胀）
    stored = {r["person_id"] for r in rows}
    for j, pid in enumerate(person_ids):
        if pid in stored:
            continue
        s = float(scores_row[j]) if j < len(scores_row) else -1.0
        if s >= min_store:
            rows.append({"photo_hash": photo_hash, "person_id": pid,
                         "score": s, "status": "unknown", "face_idx": int(face_idx)})
    return rows


def decisions_to_rows(photo_hash: str, decisions: list[tuple[int, FaceDecision]]) -> list[dict]:
    rows = []
    for fidx, d in decisions:
        rows.append(
            {
                "photo_hash": photo_hash,
                "face_idx": fidx,
                "status": d.status,
                "person_id": d.person_id,
                "score": round(d.score, 4),
                "s2": round(d.s2, 4),
                "margin": round(d.margin, 4),
                "candidates": json.dumps(d.candidates, ensure_ascii=False),
            }
        )
    return rows
