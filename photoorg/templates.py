"""底库管理：入库（注册）、回流、去重、上限淘汰、健康度诊断（§3.2 / §8.8）。"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np

from .config import AppPaths, Settings
from .db import ClassDB, PersonStore
from .engine import FaceEngine
from .imaging import crop_box, imread, imwrite
from .pipeline import UserError, load_settings
from .util import now_iso


def _gate_ok(face, gate: dict[str, float]) -> tuple[bool, str]:
    if face.det_score < gate["min_det"]:
        return False, "检测分太低"
    if face.iod and face.iod < gate["min_iod"]:
        return False, f"瞳距太小（{face.iod:.0f}px）"
    if face.quality < gate["min_quality"]:
        return False, "综合质量低"
    if face.blur < gate["min_blur"]:
        return False, "模糊"
    if abs(face.yaw) > gate["max_abs_yaw"]:
        return False, "侧脸角度太大"
    return True, ""


def dedup_against(emb: np.ndarray, matrix: np.ndarray, thresh: float) -> tuple[bool, float]:
    if matrix.shape[0] == 0:
        return False, 0.0
    sims = matrix @ emb
    best = float(sims.max())
    return best >= thresh, best


def _enforce_limit(app: AppPaths, class_name: str, person_id: int, settings: Settings) -> int:
    """模板超过上限时淘汰最旧/最差（§3.5）。"""
    store = PersonStore(app.persons_db)
    cp = app.class_paths(class_name)
    klass = store.klass(class_name)
    if not klass:
        return 0
    limit = int(settings.enroll.get("max_templates", 20))
    rows = store.templates(int(klass["id"]), person_id)
    if len(rows) <= limit:
        return 0
    rows.sort(key=lambda r: (float(r["quality"]), r["id"]))
    drop = rows[: len(rows) - limit]
    for r in drop:
        store.set_template_active(int(r["id"]), False)
        if r["path"]:
            p = cp.dir / r["path"]
            if p.exists():
                p.unlink()
    return len(drop)


def add_template_from_face(
    app: AppPaths,
    class_name: str,
    person_id: int,
    photo_hash: str,
    face_idx: int,
    *,
    source: str = "refill",
    settings: Settings | None = None,
    force: bool = False,
    force_dedup: bool = False,
) -> dict:
    """把某张照片里的某张脸变成该人的模板（含去重、质量门控、裁剪存盘）。

    force_dedup 只跳过「与已有模板太像」这一条，质量门控仍然生效——用于同一个人
    的相似照片确实需要补一张模板的情况。
    """
    settings = settings or load_settings(app, class_name)
    store = PersonStore(app.persons_db)
    cp = app.class_paths(class_name)
    cdb = ClassDB(cp.faces_db)
    klass = store.klass(class_name)
    if not klass:
        raise UserError(f"班级不存在：{class_name}")
    class_id = int(klass["id"])
    person = store.person(person_id)
    if not person:
        raise UserError("人不存在")

    faces = {f["face_idx"]: f for f in cdb.faces(photo_hash)}
    face = faces.get(int(face_idx))
    if not face:
        raise UserError("这张脸已经不在了（可能重跑过）。请在界面上重新选一张照片。")
    ph = cdb.photo(photo_hash)
    if not ph:
        raise UserError("照片记录不存在")

    emb = np.frombuffer(face["embedding"], dtype=np.float32)
    gate = settings.template_gate
    if not force:
        if face["det_score"] < gate["min_det"] or face["quality"] < gate["min_quality"]:
            raise UserError(f"这张脸质量不够，不入库（质量 {face['quality']:.2f}）")
        if float(face["iod"] or 0) and float(face["iod"]) < gate["min_iod"]:
            raise UserError(f"瞳距太小（{float(face['iod']):.0f}px），不入库")

    # 去重只与「同一个人」已有模板比较。不同人员之间再像也不应让某人的
    # 第一张参考照被当成重复而拒绝；身份冲突留给人工在底库页处理。
    existing = store.templates(class_id, person_id)
    tpl_mat = (
        np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in existing])
        if existing else np.zeros((0, emb.size), dtype=np.float32)
    )
    dup, sim = dedup_against(emb, tpl_mat, float(settings.enroll.get("dedup_sim", 0.95)))
    if dup and not force and not force_dedup:
        return {"added": False, "reason": f"与已有模板相似度 {sim:.2f}，跳过", "sim": sim}

    img = imread(Path(ph["abs"]))
    patch = crop_box(img, face["bbox"], pad=0.32)
    name = f"{now_iso().replace(':', '').replace('-', '')[:15]}_{photo_hash[:8]}_{face_idx}.jpg"
    rel = f"gallery/{person['name']}/{name}"
    dst = cp.dir / rel
    imwrite(dst, patch, quality=92)

    tid = store.add_template(
        class_id, person_id, emb,
        path=rel, photo_hash=photo_hash, face_idx=int(face_idx),
        quality=float(face["quality"]), yaw=float(face["yaw"]), iod=float(face["iod"]),
        source=source,
    )
    pruned = _enforce_limit(app, class_name, person_id, settings)
    return {"added": True, "template_id": tid, "path": str(dst), "sim": sim, "pruned": pruned}


def enroll_files(
    app: AppPaths,
    class_name: str,
    person_name: str,
    files: list[Path],
    *,
    engine: FaceEngine | None = None,
    settings: Settings | None = None,
    source: str = "manual",
    progress=None,
) -> dict:
    """人工入库：一批照片 → 每人一个模板集。照片里最大的那张脸才算，避免把别人录进来。"""
    settings = settings or load_settings(app, class_name)
    app.ensure()
    cp = app.class_paths(class_name)
    cp.ensure()
    store = PersonStore(app.persons_db)
    class_id = store.upsert_class(class_name)
    pid = store.upsert_person(person_name)
    store.add_member(class_id, pid)

    engine = engine or FaceEngine(settings)
    added, rejected = 0, []
    for i, f in enumerate(files, 1):
        if progress:
            progress(i - 1, len(files), Path(f).name)
        try:
            img = imread(Path(f))
        except Exception as e:
            rejected.append(f"{Path(f).name}: {e}")
            continue
        faces = [x for x in engine.analyze(img) if x.quality >= 0]
        if not faces:
            rejected.append(f"{Path(f).name}: 没检出脸")
            continue
        faces.sort(key=lambda x: -x.area)
        main = faces[0]
        if len(faces) > 1 and faces[1].area > main.area * 0.7:
            rejected.append(f"{Path(f).name}: 有多张差不多大的脸，不确定是谁（请单独裁一张）")
            continue
        ok, why = _gate_ok(main, settings.template_gate)
        if not ok:
            rejected.append(f"{Path(f).name}: {why}")
            continue
        # 只对该人员自身的模板去重，不能拿其它同学的脸阻止当前人员入库。
        existing = store.templates(class_id, pid)
        tpl_mat = (
            np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in existing])
            if existing else np.zeros((0, main.embedding.size), dtype=np.float32)
        )
        dup, sim = dedup_against(main.embedding, tpl_mat, float(settings.enroll.get("dedup_sim", 0.95)))
        if dup:
            rejected.append(f"{Path(f).name}: 与已有模板太像（{sim:.2f}），跳过")
            continue
        patch = crop_box(img, main.bbox, pad=0.32)
        name = f"{now_iso().replace(':', '').replace('-', '')[:15]}_{i:03d}.jpg"
        rel = f"gallery/{person_name}/{name}"
        dst = cp.dir / rel
        imwrite(dst, patch)
        store.add_template(
            class_id, pid, main.embedding, path=rel, quality=main.quality,
            yaw=main.yaw, iod=main.iod, source=source,
        )
        added += 1
    _enforce_limit(app, class_name, pid, settings)
    if progress:
        progress(len(files), len(files), "")
    return {"person": person_name, "added": added, "rejected": rejected}


def enroll_from_dirs(
    app: AppPaths,
    class_name: str,
    roster_root: Path,
    *,
    engine: FaceEngine | None = None,
    settings: Settings | None = None,
) -> dict:
    """从一个「人名/照片」目录树批量入库（roster/ 或 gallery/ 都行）。"""
    roster_root = Path(roster_root)
    if not roster_root.is_dir():
        raise UserError(f"目录不存在：{roster_root}")
    settings = settings or load_settings(app, class_name)
    engine = engine or FaceEngine(settings)
    results = []
    for sub in sorted(p for p in roster_root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        files = sorted(
            p for p in sub.rglob("*")
            if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
        )
        if not files:
            continue
        results.append(
            enroll_files(app, class_name, sub.name, files, engine=engine,
                         settings=settings, source="manual")
        )
    return {"persons": results}


def health(app: AppPaths, class_name: str) -> list[dict]:
    """底库健康度：只看「有证据的」指标，不做无根据的数字。"""
    store = PersonStore(app.persons_db)
    cp = app.class_paths(class_name)
    klass = store.klass(class_name)
    if not klass:
        return []
    class_id = int(klass["id"])
    out = []
    for m in store.roster(class_id):
        pid = int(m["id"])
        rows = store.templates(class_id, pid)
        warnings = []
        if not rows:
            warnings.append("底库为空")
        else:
            q = sum(float(r["quality"]) for r in rows) / len(rows)
            yaws = [abs(float(r["yaw"])) for r in rows]
            if q < 0.5:
                warnings.append(f"模板平均质量 {q:.2f} 偏低")
            if all(y < 12 for y in yaws) and len(rows) >= 3:
                warnings.append("模板全是正脸，现场多为侧脸时容易漏")
            if len(rows) < 3:
                warnings.append(f"只有 {len(rows)} 张模板，建议 3~10 张")
            oldest = min(r["created_at"] for r in rows)
            try:
                oldest_year = int(oldest[:4])
                current_year = int(now_iso()[:4])
                if oldest_year <= current_year - 2:
                    warnings.append(f"最老的模板是 {oldest[:10]}，孩子长相可能已变")
            except (ValueError, TypeError):
                # 兼容历史数据里没有规范时间戳的情况；不影响主流程。
                pass
        out.append(
            {
                "person_id": pid,
                "name": m["name"],
                "n_templates": len(rows),
                "quality": round(sum(float(r["quality"]) for r in rows) / len(rows), 3) if rows else 0.0,
                "warnings": warnings,
            }
        )
    return out


def delete_person_with_gallery(app: AppPaths, class_name: str, person_id: int) -> None:
    """删人：连模板目录一起删（危险操作，调用方负责二次确认）。"""
    store = PersonStore(app.persons_db)
    cp = app.class_paths(class_name)
    person = store.person(person_id)
    if not person:
        raise UserError("人不存在")
    d = cp.person_gallery(person["name"])
    if d.exists():
        shutil.rmtree(d)
    klass = store.klass(class_name)
    if klass:
        store.remove_member(int(klass["id"]), person_id)
    store.delete_person(person_id)
