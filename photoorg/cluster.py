"""底库冷启动：无监督聚类 → 人工敲名字 → 变成底库（§10）。

不用 HDBSCAN（多一个依赖、参数还难调），直接 kNN 图 + 并查集：
余弦相似度 ≥ 阈值就连边，连通分量即簇。40~100 人的班级数据完全够用。
合并簇 = 把两个簇一起命名；拆簇 = 只给子集命名（都在 accept 里）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .config import AppPaths
from .db import ClassDB, OverrideStore, PersonStore
from .matching import face_uid
from .pipeline import UserError, load_settings
from .state import StateStore
from .templates import add_template_from_face


class _UF:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


@dataclass
class Cluster:
    cid: int
    members: list[dict] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def rep(self) -> dict:
        return self.members[0]


def cluster_faces(
    app: AppPaths,
    class_name: str,
    batch: str | None = None,
    *,
    threshold: float = 0.55,
    min_size: int = 2,
    only_unknown: bool = False,
    max_faces: int = 3000,
) -> list[Cluster]:
    cp = app.class_paths(class_name)
    cdb = ClassDB(cp.faces_db)
    ostore = OverrideStore(cp.overrides_db)
    store = PersonStore(app.persons_db)
    klass = store.klass(class_name)
    names = {}
    if klass:
        names = {p["id"]: p["name"] for p in store.roster(int(klass["id"]))}

    hashes, idxs, mat = cdb.face_embeddings(batch=batch, usable_only=True)
    if not hashes:
        raise UserError("这个批次没有可用的脸。先跑 photoorg run。")
    meta = []
    photos = {p["hash"]: p for p in cdb.photos(batch=batch)}
    decisions = {(d["photo_hash"], d["face_idx"]): d for d in cdb.decisions(batch=batch)}
    faces = {(f["photo_hash"], f["face_idx"]): f for f in cdb.faces()}
    assigned_pids = set()
    for i, (h, fi) in enumerate(zip(hashes, idxs)):
        d = decisions.get((h, fi))
        f = faces.get((h, fi))
        if only_unknown and d and d["status"] == "present":
            continue
        meta.append(
            {
                "photo_hash": h,
                "photo_name": photos.get(h, {}).get("name", ""),
                "face_idx": int(fi),
                "face_uid": face_uid(h, int(fi)),
                "quality": float(f["quality"]) if f else 0.0,
                "score": float(d["score"]) if d else 0.0,
                "auto_person": names.get(int(d["person_id"]), "") if d and d["person_id"] else "",
            }
        )
    if not meta:
        return []
    if len(meta) > max_faces:
        keep = np.argsort([-m["quality"] for m in meta])[:max_faces]
        meta = [meta[i] for i in keep]
        mat = mat[keep]

    sims = mat @ mat.T
    uf = _UF(len(meta))
    thr = float(threshold)
    for i in range(len(meta)):
        row = sims[i]
        for j in np.nonzero(row >= thr)[0]:
            if j > i:
                uf.union(i, int(j))

    groups: dict[int, list[tuple[int, dict]]] = {}
    for i, m in enumerate(meta):
        groups.setdefault(uf.find(i), []).append((i, m))

    clusters: list[Cluster] = []
    for root, items in groups.items():
        if len(items) < min_size:
            continue
        # 代表：与簇内其它成员平均相似度最高的那张脸（medoid）
        idx = [i for i, _ in items]
        sub = sims[np.ix_(idx, idx)]
        med = idx[int(np.argmax(sub.mean(axis=1)))]
        rep = meta[med]
        members = [m for _, m in items if m is not rep]
        members.sort(key=lambda m: (-m["quality"], m["photo_name"]))
        members.insert(0, rep)
        clusters.append(Cluster(cid=len(clusters), members=members))
    clusters.sort(key=lambda c: -c.size)
    for i, c in enumerate(clusters):
        c.cid = i
    return clusters


def accept_cluster(
    app: AppPaths,
    class_name: str,
    batch: str,
    name: str,
    members: list[dict],
    *,
    operator: str = "ui",
    max_templates: int = 6,
) -> dict:
    """给一个簇敲名字 → 建人 + 入底库 + 把簇内人脸写成「出现」。"""
    name = (name or "").strip()
    if not name:
        raise UserError("得给这个簇一个名字")
    if not members:
        raise UserError("簇是空的")
    cp = app.class_paths(class_name)
    store = PersonStore(app.persons_db)
    cdb = ClassDB(cp.faces_db)
    ostore = OverrideStore(cp.overrides_db)
    sstore = StateStore(cp.batch_state)
    class_id = store.upsert_class(class_name)
    pid = store.upsert_person(name)
    store.add_member(class_id, pid)

    added, skipped = 0, []
    for m in members:
        try:
            r = add_template_from_face(
                app, class_name, pid, m["photo_hash"], m["face_idx"], source="cluster",
            )
            if r.get("added"):
                added += 1
        except UserError as e:
            skipped.append(str(e))
        if added >= max_templates:
            break
    for m in members:
        ostore.add(
            "photo_person", "present", photo_hash=m["photo_hash"], person_id=pid,
            value="present", source="cluster", operator=operator,
            reason=f"冷启动聚类命名：{name}", batch=batch,
        )
        ostore.add(
            "face_assign", "assign", photo_hash=m["photo_hash"], person_id=pid,
            face_uid=m["face_uid"], value=str(pid), source="cluster",
            operator=operator, reason="冷启动聚类命名", batch=batch,
        )
    sstore.note_edit(batch, by=operator, note=f"聚类命名 {name}（{len(members)} 张脸）")
    return {"person_id": pid, "name": name, "templates_added": added,
            "faces_assigned": len(members), "skipped": skipped[:5]}
