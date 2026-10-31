"""人工修正（§8.2）：所有入口（UI / CLI / CSV）最终都走这里，保证副作用一致：

1. 写 overrides（append-only，带 operator / reason / source）；
2. 必要时连带修正「照片×人」层（主力手段），保证不会出现「改了脸、名单没变」；
3. 正样本回流底库（§8.8①）；
4. PUBLISHED 状态自动回退（§8.6）。
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from .config import AppPaths
from .db import ClassDB, OverrideStore, PersonStore
from .matching import face_uid
from .pipeline import UserError, load_settings
from .state import StateStore
from .templates import add_template_from_face


def _stores(app: AppPaths, class_name: str):
    cp = app.class_paths(class_name)
    return (
        cp,
        PersonStore(app.persons_db),
        ClassDB(cp.faces_db),
        OverrideStore(cp.overrides_db),
        StateStore(cp.batch_state),
    )


def _face_present_candidates(cdb: ClassDB, photo_hash: str, person_id: int) -> int:
    """这张照片里有多少张脸被自动判给了这个人。（用来判断改判是否会误伤）"""
    n = 0
    for d in cdb.decisions():
        if d["photo_hash"] != photo_hash:
            continue
        if d["status"] in ("present", "review") and d["person_id"] == int(person_id):
            n += 1
    return n


def set_photo_person(
    app: AppPaths,
    class_name: str,
    batch: str,
    photo_hash: str,
    person_id: int,
    action: str,
    *,
    source: str = "ui",
    operator: str = "",
    reason: str = "",
) -> dict:
    """照片×人层的直接修正：这张照片里「有/没有」这个人。"""
    if action not in ("present", "absent"):
        raise UserError("action 只能是 present 或 absent")
    cp, store, cdb, ostore, sstore = _stores(app, class_name)
    if not cdb.photo(photo_hash):
        raise UserError("照片不存在")
    if not store.person(person_id):
        raise UserError("人不存在")
    oid = ostore.add(
        "photo_person", action, photo_hash=photo_hash, person_id=person_id,
        value=action, source=source, operator=operator, reason=reason, batch=batch,
    )
    sstore.note_edit(batch, by=operator, note=f"photo_person {action}")
    return {"override_id": oid, "action": action}


def clear_photo_person(app: AppPaths, class_name: str, batch: str, photo_hash: str, person_id: int) -> dict:
    """撤销某条照片×人的人工结论（回落到自动判定）。"""
    cp, store, cdb, ostore, sstore = _stores(app, class_name)
    row = ostore.latest_for_key("photo_person", photo_hash, person_id)
    if not row:
        return {"revoked": False}
    ostore.supersede(int(row["id"]))
    sstore.note_edit(batch, by="ui", note="撤销照片×人修正")
    return {"revoked": True, "override_id": row["id"]}


def decide_face(
    app: AppPaths,
    class_name: str,
    batch: str,
    photo_hash: str,
    face_idx: int,
    action: str,
    *,
    person_id: int | None = None,
    new_name: str | None = None,
    operator: str = "",
    reason: str = "",
    source: str = "ui",
    refill: bool = True,
) -> dict:
    """灰区复核页的一次点击。action: present | new | unknown | skip"""
    cp, store, cdb, ostore, sstore = _stores(app, class_name)
    klass = store.klass(class_name)
    if not klass:
        raise UserError("班级不存在")
    class_id = int(klass["id"])
    photo = cdb.photo(photo_hash)
    if not photo:
        raise UserError("照片不存在")
    uid = face_uid(photo_hash, int(face_idx))
    faces = {f["face_idx"]: f for f in cdb.faces(photo_hash)}
    face = faces.get(int(face_idx))
    if not face:
        raise UserError("这张脸不在了（可能重跑过，界面会刷新）")

    dec = next(
        (d for d in cdb.decisions(batch=batch)
         if d["photo_hash"] == photo_hash and d["face_idx"] == int(face_idx)),
        None,
    )
    old_pid = dec["person_id"] if dec else None
    old_status = dec["status"] if dec else "unknown"

    if action == "skip":
        return {"skipped": True}

    if action == "new":
        if not new_name or not new_name.strip():
            raise UserError("新人需要名字")
        pid = store.upsert_person(new_name.strip())
        store.add_member(class_id, pid)
        action = "present"
        person_id = pid

    if action == "present" and person_id is None:
        raise UserError("没选人")
    if action not in ("present", "unknown"):
        raise UserError(f"未知动作：{action}")

    result: dict = {"face_uid": uid, "action": action}

    if action == "present":
        assert person_id is not None
        store.add_member(class_id, person_id)
        ostore.add(
            "face_assign", "assign", photo_hash=photo_hash, person_id=person_id,
            face_uid=uid, value=str(person_id), source=source,
            operator=operator, reason=reason, batch=batch,
        )
        ostore.add(
            "photo_person", "present", photo_hash=photo_hash, person_id=person_id,
            value="present", source=source, operator=operator,
            reason=reason or "灰区复核：选定此人", batch=batch,
        )
        result["person_id"] = person_id
        # 改判：把原来判给的那个人从这张照片里摘掉（仅当他只靠这张脸存在时才自动摘）
        if old_pid and int(old_pid) != int(person_id) and old_status in ("present", "review"):
            n = _face_present_candidates(cdb, photo_hash, int(old_pid))
            if n <= 1:
                ostore.add(
                    "photo_person", "absent", photo_hash=photo_hash, person_id=int(old_pid),
                    value="absent", source=source, operator=operator,
                    reason=f"改判：这张脸判给 {store.person(person_id)['name']}", batch=batch,
                )
                result["removed_person_id"] = int(old_pid)
        if refill:
            try:
                settings = load_settings(app, class_name)
                r = add_template_from_face(
                    app, class_name, person_id, photo_hash, int(face_idx),
                    source="refill", settings=settings,
                )
                result["refill"] = r
            except UserError as e:
                result["refill"] = {"added": False, "reason": str(e)}
    else:  # unknown：这张脸不属于名单上任何人
        ostore.add(
            "face_assign", "unassign", photo_hash=photo_hash, person_id=None,
            face_uid=uid, value="", source=source, operator=operator,
            reason=reason or "人工判定：不认识", batch=batch,
        )
        if old_pid and old_status in ("present", "review"):
            n = _face_present_candidates(cdb, photo_hash, int(old_pid))
            if n <= 1:
                ostore.add(
                    "photo_person", "absent", photo_hash=photo_hash, person_id=int(old_pid),
                    value="absent", source=source, operator=operator,
                    reason="人工判定：这张脸不是名单上的人", batch=batch,
                )
                result["removed_person_id"] = int(old_pid)

    sstore.note_edit(batch, by=operator, note=f"灰区复核 {action}")
    return result


def undo(app: AppPaths, class_name: str, batch: str | None = None, scope: str | None = None) -> dict:
    cp, store, cdb, ostore, sstore = _stores(app, class_name)
    row = ostore.undo_last(batch=batch, scope=scope)
    if not row:
        return {"undone": False}
    sstore.note_edit(batch or row.get("batch") or "", by="ui", note=f"撤销 #{row['id']}")
    return {"undone": True, "override": row}


# ------------------------------------------------------------------ CSV 批量修正


CSV_HEADER = ["photo", "person", "action", "reason"]


def export_overrides_csv(app: AppPaths, class_name: str, batch: str) -> str:
    cp, store, cdb, ostore, sstore = _stores(app, class_name)
    photos = {p["hash"]: p for p in cdb.photos(batch=batch)}
    people = {p["id"]: p["name"] for p in store.persons()}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_HEADER)
    for r in ostore.all(batch=batch, scope="photo_person"):
        ph = photos.get(r["photo_hash"])
        if not ph:
            continue
        w.writerow([ph["name"], people.get(int(r["person_id"]), ""), r["action"], r["reason"]])
    return buf.getvalue()


def import_overrides_csv(
    app: AppPaths,
    class_name: str,
    batch: str,
    path: Path,
    *,
    operator: str = "csv",
) -> dict:
    """``photoorg fix --csv corrections.csv``：人可读、可手改、可版本管理（§8.3-D）。"""
    cp, store, cdb, ostore, sstore = _stores(app, class_name)
    klass = store.klass(class_name)
    if not klass:
        raise UserError("班级不存在")
    photos: dict[str, dict] = {}
    for p in cdb.photos(batch=batch):
        photos.setdefault(p["name"], p)
        photos.setdefault(p["hash"], p)

    applied, skipped = 0, []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        need = {"photo", "person", "action"}
        if not need.issubset({(c or "").strip() for c in (reader.fieldnames or [])}):
            raise UserError(f"CSV 需要这几列：{','.join(CSV_HEADER)}（当前：{reader.fieldnames}）")
        for i, row in enumerate(reader, 2):
            photo = (row.get("photo") or "").strip()
            person = (row.get("person") or "").strip()
            action = (row.get("action") or "").strip().lower()
            reason = (row.get("reason") or "").strip()
            if not photo or not person or not action:
                skipped.append(f"第 {i} 行：字段不全")
                continue
            if action in ("present", "出现", "有", "yes", "1"):
                action = "present"
            elif action in ("absent", "没出现", "不出现", "无", "no", "0"):
                action = "absent"
            else:
                skipped.append(f"第 {i} 行：action「{action}」不认识")
                continue
            ph = photos.get(photo)
            if ph is None:
                skipped.append(f"第 {i} 行：本批次没有照片「{photo}」")
                continue
            pid = store.upsert_person(person)
            store.add_member(int(klass["id"]), pid)
            ostore.add(
                "photo_person", action, photo_hash=ph["hash"], person_id=pid,
                value=action, source="csv", operator=operator, reason=reason, batch=batch,
            )
            applied += 1
    if applied:
        sstore.note_edit(batch, by=operator, note=f"CSV 导入 {applied} 条")
    return {"applied": applied, "skipped": skipped}


def audit_log(app: AppPaths, class_name: str, batch: str | None = None, limit: int = 500) -> list[dict]:
    cp, store, cdb, ostore, sstore = _stores(app, class_name)
    people = {p["id"]: p["name"] for p in store.persons()}
    photos = {p["hash"]: p["name"] for p in cdb.photos(batch=batch)} if batch else {}
    out = []
    for r in ostore.history(batch=batch, limit=limit):
        out.append(
            {
                "id": r["id"],
                "at": r["created_at"],
                "scope": r["scope"],
                "action": r["action"],
                "photo": photos.get(r["photo_hash"], r["photo_hash"][:8] if r["photo_hash"] else ""),
                "person": people.get(int(r["person_id"]), "") if r["person_id"] else "",
                "source": r["source"],
                "operator": r["operator"],
                "reason": r["reason"],
                "superseded": bool(r["superseded"]),
            }
        )
    return out
