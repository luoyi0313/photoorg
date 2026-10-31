"""流水线：扫描 → 检测 → 质量门控 → 对齐 → 提特征 → 1:N 匹配 → 决策 → 归档 + 报告。

推理与决策解耦（§11.1）：推理产物落 faces.db 后，调阈值 / 换匹配策略 / 加新生
只需要重跑 ``rematch()``，秒级，不用重新推理。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from .config import AppPaths, ClassPaths, Settings
from .db import ClassDB, OverrideStore, PersonStore
from .engine import FaceEngine
from .imaging import ImageError, imread, iter_images, sha256_file
from .matching import (
    aggregate_person_scores,
    build_pairs,
    decide_face,
    decisions_to_rows,
)
from .state import AUTO_DONE, PUBLISHED, StateStore
from .util import parse_batch_name


class UserError(RuntimeError):
    """可以直接展示给人看的错误（§8.9 错误信息写人话）。"""


@dataclass
class RunStats:
    batch: str
    n_total: int = 0
    n_new: int = 0
    n_skipped: int = 0
    n_error: int = 0
    n_faces: int = 0
    n_present_pairs: int = 0
    n_review: int = 0
    elapsed: float = 0.0
    errors: list = None
    # 本次运行把哪些照片从旧批次「搬」了过来：照片按内容 hash 唯一，一个 hash 只能
    # 属于一个批次，换个批次上传就等于从旧批次移走。旧批次的结论会因此失效。
    moved_out: list = None

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["errors"] = self.errors or []
        d["moved_out"] = self.moved_out or []
        return d


def load_settings(app: AppPaths, class_name: str) -> Settings:
    cp = app.class_paths(class_name)
    raw = json.loads(cp.thresholds.read_text("utf-8")) if cp.thresholds.exists() else None
    return Settings.from_dict(raw)


def save_settings(app: AppPaths, class_name: str, settings: Settings) -> None:
    cp = app.class_paths(class_name)
    cp.ensure()
    cp.thresholds.write_text(
        json.dumps(settings.as_dict(), ensure_ascii=False, indent=2) + "\n", "utf-8"
    )


def resolve_batch_dir(app: AppPaths, batch: str | None) -> Path:
    root = app.photos
    if not root.exists():
        raise UserError(f"照片目录不存在：{root}\n先建一个 photos/<批次名>/ 放照片。")
    if batch:
        cand = root / batch
        if not cand.is_dir():
            raise UserError(f"批次不存在：{cand}")
        return cand
    dirs = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    if not dirs:
        raise UserError(f"{root} 下没有批次目录。一个活动一个文件夹，目录名 = 日期-活动名。")
    if len(dirs) == 1:
        return dirs[0]
    names = "、".join(d.name for d in dirs)
    raise UserError(f"有多个批次，请用 --batch 指定：{names}")


def _ensure_class(app: AppPaths, class_name: str) -> tuple[PersonStore, int, list[dict]]:
    store = PersonStore(app.persons_db)
    class_id = store.upsert_class(class_name)
    roster = store.roster(class_id)
    if not roster:
        raise UserError(
            f"班级「{class_name}」的名单是空的。先把人名放进底库：\n"
            f"  photoorg person add --class {class_name} 张三\n"
            f"  或直接用 photoorg enroll 从照片里聚类点名"
        )
    return store, class_id, roster


def run_batch(
    app: AppPaths,
    class_name: str,
    batch: str | None = None,
    *,
    force: bool = False,
    limit: int | None = None,
    settings: Settings | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    engine: FaceEngine | None = None,
) -> RunStats:
    app.ensure()
    batch_dir = resolve_batch_dir(app, batch)
    batch_name = batch_dir.name
    cp = app.class_paths(class_name)
    cp.ensure()

    settings = settings or load_settings(app, class_name)
    store, class_id, roster = _ensure_class(app, class_name)
    cdb = ClassDB(cp.faces_db)

    files = iter_images(batch_dir)
    if limit:
        files = files[:limit]
    if not files:
        raise UserError(f"{batch_dir} 里没找到图片（支持 jpg/png/webp/bmp/tif）")

    owned_engine = engine is None
    engine = engine or FaceEngine(settings)
    stats = RunStats(batch=batch_name, n_total=len(files), errors=[], moved_out=[])
    moved_out: dict[str, int] = {}
    t0 = time.perf_counter()

    for i, path in enumerate(files, 1):
        if progress:
            progress(i - 1, len(files), path.name)
        rel = str(path.relative_to(batch_dir))
        try:
            phash = sha256_file(path)
        except OSError as e:
            stats.n_error += 1
            stats.errors.append(f"{rel}: 读不了 ({e})")
            continue

        prev = cdb.photo(phash)
        if prev and prev["batch"] != batch_name:
            moved_out[prev["batch"]] = moved_out.get(prev["batch"], 0) + 1
        st = path.stat()
        if (
            prev
            and not force
            and prev["status"] == "ok"
            and prev["model_ver"] == engine.model_ver
        ):
            if prev["rel"] != rel or prev["abs"] != str(path) or prev["batch"] != batch_name:
                cdb.rename_photo(phash, rel, str(path))
                cdb.upsert_photo(
                    phash, batch_name, rel, str(path), path.name,
                    mtime=st.st_mtime, size=st.st_size,
                    width=prev["width"], height=prev["height"], n_faces=prev["n_faces"],
                    status="ok", model_ver=prev["model_ver"],
                    quality=prev["quality"], processed_at=prev["processed_at"],
                )
            stats.n_skipped += 1
            stats.n_faces += int(prev["n_faces"] or 0)
            continue

        try:
            img = imread(path)
        except ImageError as e:
            stats.n_error += 1
            stats.errors.append(f"{rel}: {e}")
            cdb.upsert_photo(
                phash, batch_name, rel, str(path), path.name,
                mtime=st.st_mtime, size=st.st_size,
                status="error", error=str(e), model_ver=engine.model_ver,
            )
            continue

        try:
            faces = engine.analyze(img)
        except Exception as e:  # 单张失败不整批挂掉（§11.2）
            stats.n_error += 1
            stats.errors.append(f"{rel}: 检测失败 ({type(e).__name__}: {e})")
            cdb.upsert_photo(
                phash, batch_name, rel, str(path), path.name,
                mtime=st.st_mtime, size=st.st_size,
                status="error", error=f"检测失败: {e}", model_ver=engine.model_ver,
            )
            continue

        h, w = img.shape[:2]
        cdb.replace_faces(phash, faces, engine.model_ver)
        q = float(np.mean([f.quality for f in faces])) if faces else 0.0
        cdb.upsert_photo(
            phash, batch_name, rel, str(path), path.name,
            mtime=st.st_mtime, size=st.st_size, width=w, height=h,
            n_faces=len(faces), status="ok", model_ver=engine.model_ver, quality=q,
        )
        stats.n_new += 1
        stats.n_faces += len(faces)

    if progress:
        progress(len(files), len(files), "")

    # 换模型后旧 embedding 自动失效重算（§11.2）
    stale = cdb.stale_photos(engine.model_ver)
    if stale:
        stats.errors.append(f"有 {len(stale)} 张照片是旧模型算的，已重新推理")

    match_stats = rematch(app, class_name, batch_name, settings=settings)
    stats.n_present_pairs = match_stats["n_present_pairs"]
    stats.n_review = match_stats["n_review"]
    stats.elapsed = time.perf_counter() - t0

    sstore = StateStore(cp.batch_state)
    sstore.mark_auto_done(batch_name, note=f"run: {stats.n_new} 张新增，{stats.n_skipped} 张复用")

    if moved_out:
        # 照片搬走会让旧批次的「未出现/已出现」甚至已发布名单失效。这里显式把旧批次
        # 退回待复核并重建它的 out/，否则旧批次会停在 PUBLISHED 却提示「去复核」。
        from .archive import rebuild as _rebuild  # 延迟导入：archive 依赖 pipeline

        for old_batch, n in sorted(moved_out.items()):
            stats.moved_out.append({"batch": old_batch, "n": n})
            sstore.note_edit(
                old_batch,
                by="system",
                note=f"{n} 张照片已移入批次「{batch_name}」，原结论需重新确认",
            )
            try:
                _rebuild(app, class_name, old_batch)
            except Exception as exc:  # 旧批次归档重建失败不该让本次识别失败
                stats.errors.append(f"批次「{old_batch}」归档重建失败：{exc}")

    if owned_engine:
        engine = None
    return stats


def rematch(
    app: AppPaths,
    class_name: str,
    batch: str,
    *,
    settings: Settings | None = None,
    verbose: bool = False,
) -> dict:
    """只重算匹配与决策（不碰推理）。秒级。"""
    cp = app.class_paths(class_name)
    settings = settings or load_settings(app, class_name)
    store = PersonStore(app.persons_db)
    klass = store.klass(class_name)
    if not klass:
        raise UserError(f"班级不存在：{class_name}")
    class_id = int(klass["id"])
    roster = store.roster(class_id)
    person_ids = [int(r["id"]) for r in roster]
    cdb = ClassDB(cp.faces_db)

    tpl_mat, tpl_pids, tpl_rows = store.template_matrix(class_id)
    cdb.clear_decisions(batch=batch)
    photos = [p for p in cdb.photos(batch=batch) if p["status"] == "ok"]
    n_present = n_review = 0
    if not person_ids:
        raise UserError(f"班级「{class_name}」名单为空")

    def _finished(result: dict) -> dict:
        # 重新匹配会改变「出现/未出现」结论；已发布的名单不能继续冒充有效。
        sstore = StateStore(cp.batch_state)
        if sstore.get(batch)["state"] == PUBLISHED:
            sstore.note_edit(
                batch, by="system", note="底库或名单变化后重新匹配，原发布结论失效"
            )
        return result

    if tpl_mat.shape[0] == 0:
        # 没有底库：全部人脸都不认识，但明细照写（否则界面是空的，人会以为坏了）
        for ph in photos:
            ds = []
            for f in cdb.faces(ph["hash"]):
                d = decide_face(np.zeros((0,), dtype=np.float32), person_ids, settings)
                ds.append((f["face_idx"], d))
            cdb.set_decisions(decisions_to_rows(ph["hash"], ds))
        return _finished(
            {"n_present_pairs": 0, "n_review": 0, "n_photos": len(photos), "no_gallery": True}
        )

    all_rows = []
    for ph in photos:
        faces = cdb.faces(ph["hash"])
        decisions = []
        pair_rows = []
        if faces:
            fm = np.stack([np.frombuffer(f["embedding"], dtype=np.float32) for f in faces])
            scores = aggregate_person_scores(fm, tpl_mat, tpl_pids, person_ids)
            for i, f in enumerate(faces):
                if not f["usable"]:
                    d = decide_face(scores[i], person_ids, settings)
                    # 不可信的脸：保留候选做证据，但绝不自动归档
                    status = "review" if float(scores[i].max(initial=-1)) >= settings.t_low else "unknown"
                    d.status = status
                    d.reason = "质量不达标：" + (f["reasons"] or "")
                    decisions.append((f["face_idx"], d))
                    continue
                d = decide_face(scores[i], person_ids, settings)
                decisions.append((f["face_idx"], d))
            # 用可用的脸的分数聚合 photo×person；不可用脸只做证据
            usable_idx = [i for i, f in enumerate(faces) if f["usable"]]
            if usable_idx:
                agg = scores[usable_idx].max(axis=0)
            else:
                agg = scores.max(axis=0)
            for i, f in enumerate(faces):
                if f["usable"]:
                    pair_rows.extend(
                        build_pairs(ph["hash"], [(f["face_idx"], decisions[i][1])], scores[i],
                                    person_ids, face_idx=f["face_idx"])
                    )
            pair_rows = _merge_pair_rows(pair_rows)
            if not pair_rows:
                pair_rows = build_pairs(ph["hash"], [], agg, person_ids, face_idx=-1)
        cdb.set_decisions(decisions_to_rows(ph["hash"], decisions))
        cdb.set_pairs(pair_rows)
        all_rows.extend(decisions_to_rows(ph["hash"], decisions))
        n_present += sum(1 for r in pair_rows if r["status"] == "present")
        n_review += sum(1 for r in pair_rows if r["status"] == "review")

    return _finished({"n_present_pairs": n_present, "n_review": n_review, "n_photos": len(photos)})


def _merge_pair_rows(rows: list[dict]) -> list[dict]:
    """同一张照片里同一个人出现多次 → 只保留最高分一条（(photo,person) 唯一，§1.3）。"""
    best: dict[tuple, dict] = {}
    rank = {"present": 2, "review": 1, "unknown": 0}
    for r in rows:
        key = (r["photo_hash"], r["person_id"])
        cur = best.get(key)
        if cur is None:
            best[key] = r
            continue
        if rank[r["status"]] > rank[cur["status"]] or (
            rank[r["status"]] == rank[cur["status"]] and r["score"] > cur["score"]
        ):
            best[key] = r
    return list(best.values())


def batch_meta(batch: str) -> dict:
    date, activity = parse_batch_name(batch)
    return {"batch": batch, "date": date, "activity": activity}
