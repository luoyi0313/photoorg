"""跨批次统计：活动 × 人员出勤矩阵、按人汇总所有活动照片。

只读派生数据，随时可以由 faces.db + overrides.db 重新算出来。
"""
from __future__ import annotations

import csv
import io

from .config import AppPaths
from .db import ClassDB, OverrideStore, PersonStore
from .util import parse_batch_name


def _batch_day(batch: str) -> str:
    """批次名 → 'YYYY-MM-DD'，解析不出来是空串。"""
    return parse_batch_name(batch)[0]


def _keep(batch: str, date_from: str, date_to: str) -> bool:
    day = _batch_day(batch)
    if not day:
        # 没有日期的批次无法参与时间筛选：不筛选时保留，筛选时排除。
        return not (date_from or date_to)
    if date_from and day < date_from:
        return False
    if date_to and day > date_to:
        return False
    return True


def _present_map(cdb: ClassDB, ostore: OverrideStore, batch: str) -> dict[tuple[str, int], dict]:
    """最终「照片×人=出现」集合（自动结论 + 人工修正）。

    人工修正是按批次记录的：照片搬到别的批次后，旧批次里那条修正已经不再指向
    本批次存在的照片，必须忽略，否则矩阵会把搬走的照片重复算进来。
    """
    out: dict[tuple[str, int], dict] = {}
    in_batch = {ph["hash"] for ph in cdb.photos(batch=batch)}
    for r in cdb.pairs(batch=batch):
        if r["status"] == "present":
            out[(r["photo_hash"], int(r["person_id"]))] = {
                "score": float(r["score"]),
                "face_idx": int(r["face_idx"]),
                "manual": False,
            }
    for (h, pid), o in ostore.photo_person_map(batch=batch).items():
        if h not in in_batch:
            continue
        if o["action"] == "absent":
            out.pop((h, pid), None)
        elif o["action"] == "present":
            cur = out.get((h, pid), {})
            out[(h, pid)] = {
                "score": cur.get("score", 0.0),
                "face_idx": cur.get("face_idx", -1),
                "manual": True,
            }
    return out


def _context(app: AppPaths, class_name: str):
    store = PersonStore(app.persons_db)
    klass = store.klass(class_name)
    if not klass:
        raise ValueError(f"班级不存在：{class_name}")
    class_id = int(klass["id"])
    cp = app.class_paths(class_name)
    return store, class_id, ClassDB(cp.faces_db), OverrideStore(cp.overrides_db)


def _batches(cdb: ClassDB, date_from: str = "", date_to: str = "") -> list[dict]:
    rows = []
    for b in cdb.batches():
        if not _keep(b["batch"], date_from, date_to):
            continue
        day, activity = parse_batch_name(b["batch"])
        rows.append(
            {
                "batch": b["batch"],
                "date": day,
                "activity": activity,
                "n_photos": int(b["n_photos"] or 0),
            }
        )
    rows.sort(key=lambda r: (r["date"] or "9999-99-99", r["batch"]))
    return rows


def class_matrix(
    app: AppPaths,
    class_name: str,
    date_from: str = "",
    date_to: str = "",
) -> dict:
    """活动为行、人员为列的出勤矩阵。"""
    store, class_id, cdb, ostore = _context(app, class_name)
    roster = store.roster(class_id)
    persons = [{"person_id": int(r["id"]), "name": r["name"]} for r in roster]
    batches = _batches(cdb, date_from, date_to)

    counts: dict[int, dict[str, int]] = {
        p["person_id"]: {b["batch"]: 0 for b in batches} for p in persons
    }
    for b in batches:
        for (_h, pid) in _present_map(cdb, ostore, b["batch"]):
            row = counts.get(pid)
            if row is not None and b["batch"] in row:
                row[b["batch"]] += 1

    person_rows = []
    for p in persons:
        row = counts[p["person_id"]]
        person_rows.append(
            {
                "person_id": p["person_id"],
                "name": p["name"],
                "counts": row,
                "total": sum(row.values()),
            }
        )

    return {
        "class": class_name,
        "from": date_from,
        "to": date_to,
        "batches": batches,
        "persons": person_rows,
        "n_photos_total": sum(b["n_photos"] for b in batches),
        "n_pairs_total": sum(p["total"] for p in person_rows),
    }


def person_photos(
    app: AppPaths,
    class_name: str,
    person_id: int,
    date_from: str = "",
    date_to: str = "",
) -> dict:
    """某个人在所有（或时间范围内）活动里出现过的照片，按活动分组。"""
    store, class_id, cdb, ostore = _context(app, class_name)
    roster = {int(r["id"]): r for r in store.roster(class_id)}
    if int(person_id) not in roster:
        raise ValueError("这个班没有这个人员")
    person = roster[int(person_id)]
    batches = _batches(cdb, date_from, date_to)

    groups = []
    for b in batches:
        photos = {ph["hash"]: ph for ph in cdb.photos(batch=b["batch"])}
        entries = _present_map(cdb, ostore, b["batch"])
        items = []
        for (h, pid), info in entries.items():
            if pid != int(person_id):
                continue
            ph = photos.get(h)
            if not ph:
                continue
            items.append(
                {
                    "photo_hash": h,
                    "name": ph["name"],
                    "face_idx": info["face_idx"],
                    "score": round(info["score"], 3),
                    "manual": info["manual"],
                    "status": ph["status"],
                }
            )
        if items:
            items.sort(key=lambda x: x["name"])
            groups.append(
                {
                    "batch": b["batch"],
                    "date": b["date"],
                    "activity": b["activity"],
                    "count": len(items),
                    "photos": items,
                }
            )
    return {
        "class": class_name,
        "person_id": int(person_id),
        "name": person["name"],
        "from": date_from,
        "to": date_to,
        "total": sum(g["count"] for g in groups),
        "groups": groups,
    }


def matrix_csv(matrix: dict) -> str:
    """活动为行、人员为列的 CSV；Excel 直接打开需要 UTF-8 BOM。"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    names = [p["name"] for p in matrix["persons"]]
    writer.writerow(["活动", "日期"] + names + ["照片数"])
    for b in matrix["batches"]:
        writer.writerow(
            [b["activity"] or b["batch"], b["date"]]
            + [p["counts"][b["batch"]] for p in matrix["persons"]]
            + [b["n_photos"]]
        )
    writer.writerow(
        ["合计", ""]
        + [p["total"] for p in matrix["persons"]]
        + [matrix["n_photos_total"]]
    )
    return "\ufeff" + buf.getvalue()
