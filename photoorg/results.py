"""把「不可变推理产物 ⨝ 人工覆盖」合并成最终结论（§8.4 的 final_status）。

所有对外产物（归档目录、report.md、report.csv、Web 页面）都只从这里取数，
避免出现「界面上改了、报告没变」这种不一致。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .config import AppPaths, Settings
from .db import ClassDB, OverrideStore, PersonStore
from .matching import face_uid
from .state import PUBLISHED, StateStore

LOW_EVIDENCE_STORE = 0.20


@dataclass
class PairResult:
    photo_hash: str
    photo_name: str
    person_id: int
    person_name: str
    status: str            # present | review | absent
    auto_status: str       # present | review | unknown
    score: float
    manual: bool = False
    reason: str = ""
    override_id: int | None = None

    @property
    def source(self) -> str:
        if self.manual:
            return "manual"
        if self.auto_status == "present":
            return f"auto ({self.score:.2f})"
        if self.auto_status == "review":
            return f"review ({self.score:.2f})"
        return "absent"


@dataclass
class PersonResult:
    person_id: int
    name: str
    photos: list[PairResult] = field(default_factory=list)
    review_photos: list[PairResult] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    absent_confirmed: bool = False
    n_templates: int = 0
    template_quality: float = 0.0
    corrections: int = 0
    flagged: list[str] = field(default_factory=list)

    @property
    def present_count(self) -> int:
        return len(self.photos)

    @property
    def manual_count(self) -> int:
        return sum(1 for p in self.photos if p.manual)

    @property
    def auto_count(self) -> int:
        return self.present_count - self.manual_count

    @property
    def absent(self) -> bool:
        return self.present_count == 0

    @property
    def recall_hint(self) -> str:
        total = self.present_count + len(self.review_photos)
        if total == 0:
            return "100%"
        return f"{self.present_count / total * 100:.0f}%"


@dataclass
class BatchView:
    class_name: str
    batch: str
    photos: list[dict] = field(default_factory=list)
    pairs: dict = field(default_factory=dict)          # (hash, pid) -> PairResult
    persons: list[PersonResult] = field(default_factory=list)
    review_items: list[dict] = field(default_factory=list)
    unknown_faces: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)
    settings: Settings | None = None
    roster_unconfirmed: list[PersonResult] = field(default_factory=list)

    # -- 便捷
    @property
    def n_present_pairs(self) -> int:
        return sum(p.present_count for p in self.persons)

    @property
    def absent_persons(self) -> list[PersonResult]:
        return [p for p in self.persons if p.absent]

    @property
    def can_publish(self) -> bool:
        return self.review_ready and self.absent_ready

    @property
    def review_ready(self) -> bool:
        return len(self.review_items) == 0

    @property
    def absent_ready(self) -> bool:
        return all(p.absent_confirmed for p in self.persons if p.absent)

    @property
    def draft_banner(self) -> str:
        if self.state.get("state") == PUBLISHED:
            return ""
        bits = []
        if self.review_items:
            bits.append(f"{len(self.review_items)} 条灰区未确认")
        unconfirmed = [p.name for p in self.persons if p.absent and not p.absent_confirmed]
        if unconfirmed:
            bits.append(f"{len(unconfirmed)} 人「未出现」未确认")
        if self.stats.get("incomplete"):
            bits.append("批次质量不达标，本期不出「未出现」结论")
        if not bits:
            bits.append("已可定稿")
        return "草稿 · " + " · ".join(bits) + " · 请勿对外发送"


def _pair(source_status: str) -> str:
    return source_status


def compute_batch(
    app: AppPaths,
    class_name: str,
    batch: str,
    settings: Settings | None = None,
) -> BatchView:
    cp = app.class_paths(class_name)
    store = PersonStore(app.persons_db)
    cdb = ClassDB(cp.faces_db)
    ostore = OverrideStore(cp.overrides_db)
    sstore = StateStore(cp.batch_state)

    settings = settings or Settings.from_dict(
        json.loads(cp.thresholds.read_text("utf-8")) if cp.thresholds.exists() else None
    )
    klass = store.klass(class_name)
    class_id = int(klass["id"]) if klass else 0
    roster = store.roster(class_id) if class_id else []
    names = {r["id"]: r["name"] for r in roster}

    photos = cdb.photos(batch=batch)
    view = BatchView(class_name=class_name, batch=batch, photos=photos, settings=settings)
    view.state = sstore.get(batch)

    auto_pairs = {(r["photo_hash"], int(r["person_id"])): r for r in cdb.pairs(batch=batch)}
    manual = ostore.photo_person_map(batch=batch)
    confirmations = sstore.confirmations(batch)

    # ---------- 每人对每张照片的最终结论
    per_person: dict[int, dict] = {r["id"]: {"present": [], "review": [], "correct": 0} for r in roster}
    for ph in photos:
        if ph["status"] == "error":
            continue
        for p in roster:
            pid = int(p["id"])
            key = (ph["hash"], pid)
            ov = manual.get(key)
            ap = auto_pairs.get(key)
            auto_status = ap["status"] if ap else "unknown"
            auto_score = float(ap["score"]) if ap else -1.0
            if ov is not None:
                act = ov["action"]
                if act == "present":
                    pr = PairResult(
                        ph["hash"], ph["name"], pid, p["name"], "present", auto_status,
                        auto_score, True, ov.get("reason", ""), ov["id"],
                    )
                    per_person[pid]["present"].append(pr)
                else:  # absent
                    pr = PairResult(
                        ph["hash"], ph["name"], pid, p["name"], "absent", auto_status,
                        auto_score, True, ov.get("reason", ""), ov["id"],
                    )
                    view.pairs[key] = pr
                continue
            if auto_status == "present":
                pr = PairResult(ph["hash"], ph["name"], pid, p["name"], "present",
                                "present", auto_score)
                per_person[pid]["present"].append(pr)
                view.pairs[key] = pr
            elif auto_status == "review":
                pr = PairResult(ph["hash"], ph["name"], pid, p["name"], "review",
                                "review", auto_score)
                per_person[pid]["review"].append(pr)
                view.pairs[key] = pr

    # ---------- 未出现的反证（top3 最像的脸）+ 顺序按字母/学号稳定
    for p in roster:
        pid = int(p["id"])
        pr = PersonResult(person_id=pid, name=p["name"])
        pr.photos = sorted(per_person[pid]["present"], key=lambda x: x.photo_name)
        pr.review_photos = sorted(per_person[pid]["review"], key=lambda x: x.photo_name)
        pr.absent_confirmed = pid in confirmations
        tpls = store.templates(class_id, pid)
        pr.n_templates = len(tpls)
        if tpls:
            pr.template_quality = sum(float(t["quality"]) for t in tpls) / len(tpls)
        pr.corrections = sum(
            1 for r in ostore.all(batch=batch) if r["person_id"] == pid and r["scope"] == "photo_person"
        )
        if pr.absent and not pr.review_photos:
            ev = [
                row for row in cdb.pairs(batch=batch, person_id=pid)
                if row["status"] in ("review", "unknown")
            ]
            ev.sort(key=lambda r: -float(r["score"]))
            photo_name = {ph["hash"]: ph["name"] for ph in photos}
            pr.evidence = [
                {
                    "photo_hash": r["photo_hash"],
                    "photo_name": photo_name.get(r["photo_hash"], ""),
                    "score": round(float(r["score"]), 3),
                    "status": r["status"],
                    "face_idx": int(r.get("face_idx", -1)),
                }
                for r in ev[:3]
            ]
        # 底库健康度提示（启发式，不假装精确）
        if pr.n_templates == 0:
            pr.flagged.append("底库为空")
        elif pr.template_quality < 0.45:
            pr.flagged.append("底库模板质量偏低")
        if pr.review_photos and not pr.photos:
            pr.flagged.append("本批次全是灰区，建议补模板")
        if pr.corrections >= 2:
            pr.flagged.append("人工改判较多，检查底库是否过期")
        view.persons.append(pr)

    # ---------- 灰区待确认清单（人脸层）
    if class_id:
        face_assign = ostore.face_map(batch=batch)
        decisions = cdb.decisions(batch=batch)
        faces = {(r["photo_hash"], r["face_idx"]): r for r in cdb.faces()}
        photo_name = {ph["hash"]: ph for ph in photos}
        for d in decisions:
            uid = face_uid(d["photo_hash"], d["face_idx"])
            f = faces.get((d["photo_hash"], d["face_idx"]))
            cands = json.loads(d["candidates"] or "[]")
            resolved = uid in face_assign
            if not resolved and d["person_id"] is not None:
                resolved = (d["photo_hash"], int(d["person_id"])) in manual
            if d["status"] == "review" and not resolved:
                ph = photo_name.get(d["photo_hash"], {})
                view.review_items.append(
                    {
                        "face_uid": uid,
                        "photo_hash": d["photo_hash"],
                        "photo_name": ph.get("name", ""),
                        "face_idx": d["face_idx"],
                        "bbox": f["bbox"] if f else [],
                        "quality": round(float(f["quality"]), 3) if f else 0.0,
                        "usable": bool(f["usable"]) if f else False,
                        "reasons": (f["reasons"] if f else ""),
                        "score": round(float(d["score"]), 3),
                        "margin": round(float(d["margin"]), 3),
                        "candidates": [
                            {"person_id": c["person_id"],
                             "name": names.get(c["person_id"], f"#{c['person_id']}"),
                             "score": c["score"]}
                            for c in cands
                        ],
                    }
                )
            if d["status"] == "unknown" and f and f["usable"] and not resolved:
                view.unknown_faces.append(
                    {
                        "face_uid": uid,
                        "photo_hash": d["photo_hash"],
                        "photo_name": photo_name.get(d["photo_hash"], {}).get("name", ""),
                        "face_idx": d["face_idx"],
                        "score": round(float(d["score"]), 3),
                        "candidates": [
                            {"person_id": c["person_id"],
                             "name": names.get(c["person_id"], f"#{c['person_id']}"),
                             "score": c["score"]}
                            for c in cands
                        ],
                    }
                )
        view.review_items.sort(key=lambda r: (r["photo_name"], r["face_idx"]))
        view.unknown_faces.sort(key=lambda r: -r["score"])

    # ---------- 批次统计 / 质量门
    n_photos = len(photos)
    n_error = sum(1 for ph in photos if ph["status"] == "error")
    all_faces = cdb.faces() if n_photos else []
    batch_hashes = {ph["hash"] for ph in photos}
    faces = [f for f in all_faces if f["photo_hash"] in batch_hashes]
    n_faces = len(faces)
    n_usable = sum(1 for f in faces if f["usable"])
    fail_ratio = n_error / n_photos if n_photos else 0.0
    unusable_ratio = (1 - n_usable / n_faces) if n_faces else 0.0
    gate = settings.batch_gate
    incomplete = (fail_ratio > gate["max_fail_ratio"]) or (unusable_ratio > gate["max_unusable_ratio"])
    view.stats = {
        "n_photos": n_photos,
        "n_error": n_error,
        "n_faces": n_faces,
        "n_usable_faces": n_usable,
        "n_unknown_faces": len(view.unknown_faces),
        "n_review": len(view.review_items),
        "n_pairs_present": view.n_present_pairs,
        "n_manual": len(manual),
        "n_absent": len(view.absent_persons),
        "avg_faces_per_photo": round(n_faces / n_photos, 2) if n_photos else 0.0,
        "fail_ratio": round(fail_ratio, 3),
        "unusable_ratio": round(unusable_ratio, 3),
        "max_face_w": round(max((f["face_w"] for f in faces), default=0.0), 1),
        "incomplete": incomplete,
        "incomplete_reason": (
            f"检测失败率 {fail_ratio:.0%} / 质量不达标率 {unusable_ratio:.0%} 超过门限"
            if incomplete else ""
        ),
    }
    view.roster_unconfirmed = [p for p in view.persons if p.absent and not p.absent_confirmed]
    return view


def person_photos(view: BatchView, person_id: int) -> list[dict]:
    out = []
    for p in view.pairs.values():
        if p.person_id == person_id:
            out.append(p)
    return out


def photo_faces_detail(
    app: AppPaths, class_name: str, photo_hash: str
) -> list[dict]:
    """某张照片里每张脸的判定 + 候选（给「按照片看」用）。"""
    cp = app.class_paths(class_name)
    cdb = ClassDB(cp.faces_db)
    store = PersonStore(app.persons_db)
    klass = store.klass(class_name)
    names = {r["id"]: r["name"] for r in store.roster(int(klass["id"]))} if klass else {}
    ostore = OverrideStore(cp.overrides_db)
    fa = ostore.face_map()
    dec = {(d["photo_hash"], d["face_idx"]): d
           for d in cdb.decisions() if d["photo_hash"] == photo_hash}
    out = []
    for f in cdb.faces(photo_hash):
        d = dec.get((photo_hash, f["face_idx"]), {})
        uid = face_uid(photo_hash, f["face_idx"])
        ov = fa.get(uid)
        out.append(
            {
                "face_idx": f["face_idx"],
                "face_uid": uid,
                "bbox": f["bbox"],
                "quality": round(float(f["quality"]), 3),
                "usable": bool(f["usable"]),
                "reasons": f["reasons"],
                "status": d.get("status", "unknown"),
                "person_id": (ov or {}).get("person_id", d.get("person_id")),
                "person_name": names.get(
                    int((ov or {}).get("person_id") or d.get("person_id") or -1), ""
                ),
                "score": round(float(d.get("score", 0.0)), 3),
                "manual": ov is not None,
                "candidates": [
                    {"person_id": c["person_id"],
                     "name": names.get(c["person_id"], f"#{c['person_id']}"),
                     "score": c["score"]}
                    for c in json.loads(d.get("candidates") or "[]")
                ],
                "crop": f"/api/crop/{photo_hash}/{f['face_idx']}",
            }
        )
    return out
