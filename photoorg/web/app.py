"""本地 HTTP 服务 + Web UI（127.0.0.1:8765）。

三个页面（§8.3）：
- /review  灰区复核页（系统问你，键盘驱动）
- /inspect 结果审查页（你主动看：按人 / 按照片）
- /gallery 底库管理页（长期维护）
再加总览与设置（阈值校准、定稿门禁）。
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__
from ..archive import adopt as adopt_impl
from ..archive import diff_auto_vs_manual
from ..archive import rebuild as rebuild_impl
from ..calibrate import apply as apply_thresholds
from ..calibrate import suggest as suggest_thresholds
from ..cluster import accept_cluster, cluster_faces
from ..config import AppPaths, IMAGE_EXTS, Settings, default_model_root, safe_name
from ..corrections import (
    audit_log,
    clear_photo_person,
    decide_face,
    export_overrides_csv,
    set_photo_person,
    undo as undo_impl,
)
from ..db import ClassDB, OverrideStore, PersonStore
from ..engine import FaceEngine
from ..imaging import crop_box, imread, iter_images, jpeg_bytes, resize_max, safe_photo_name, sha256_file
from ..matching import face_uid
from ..pipeline import UserError, batch_meta, load_settings, rematch, resolve_batch_dir, run_batch, save_settings
from ..report import build_csv, build_markdown
from ..results import compute_batch, photo_faces_detail
from ..state import PUBLISHED, REVIEWED, RUNNING, StateStore
from ..stats import class_matrix, matrix_csv, person_photos
from ..templates import (
    add_template_from_face,
    delete_person_with_gallery,
    enroll_files,
    health as gallery_health,
)
from ..util import now_human, now_iso, parse_batch_name

STATIC = Path(__file__).parent / "static"


# --------------------------------------------------------------- 请求模型


class DecideReq(BaseModel):
    batch: str
    photo_hash: str
    face_idx: int
    action: str                    # present | new | unknown | skip
    person_id: int | None = None
    new_name: str | None = None
    reason: str = ""
    operator: str = ""


class PairReq(BaseModel):
    batch: str
    photo_hash: str
    person_id: int
    action: str                    # present | absent
    reason: str = ""
    operator: str = ""


class GalleryAddReq(BaseModel):
    batch: str
    photo_hash: str
    face_idx: int
    person_id: int
    force: bool = False


class ConfirmReq(BaseModel):
    batch: str
    person_id: int
    confirmed: bool = True
    by: str = ""


class PublishReq(BaseModel):
    batch: str
    by: str = ""


class RebuildReq(BaseModel):
    batch: str
    dry_run: bool = False


class BatchReq(BaseModel):
    batch: str


class CalibrateApplyReq(BaseModel):
    batch: str = ""
    t_high: float
    t_low: float
    margin: float | None = None
    by: str = "ui"


class ClusterAcceptReq(BaseModel):
    batch: str
    name: str
    members: list[dict]
    operator: str = "ui"


class PersonReq(BaseModel):
    name: str
    class_name: str = ""


class RenameReq(BaseModel):
    person_id: int
    new_name: str
    class_name: str = ""


class PhotoDeleteReq(BaseModel):
    batch: str
    photo_hash: str


class BatchDeleteReq(BaseModel):
    batch: str
    delete_photos: bool = False


class ClassDeleteReq(BaseModel):
    class_name: str
    delete_photos: bool = False


# --------------------------------------------------------------- app


def create_app(root: Path | str = ".", default_class: str | None = None) -> FastAPI:
    api = FastAPI(title="photoorg", version=__version__, docs_url="/api/docs")
    app_paths = AppPaths(Path(root))

    @api.middleware("http")
    async def no_cache_api_responses(request: Request, call_next):
        """本机 UI 的数据必须反映 SQLite 当前值，不能缓存旧的发布/识别状态。"""
        response = await call_next(request)
        # API 与 HTML 外壳都不缓存：改了界面后用户刷新一次就能拿到新版本
        path = request.url.path
        if path.startswith("/api/") or path == "/" or path.endswith(".html"):
            response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response

    # ---------------- 内部工具
    def _class(name: str | None) -> str:
        if name:
            return safe_name(name)
        if default_class:
            return safe_name(default_class)
        cfg = app_paths.global_config
        if cfg.exists():
            try:
                d = json.loads(cfg.read_text("utf-8"))
                if d.get("default_class"):
                    return safe_name(d["default_class"])
            except Exception:
                pass
        store = PersonStore(app_paths.persons_db)
        cs = store.classes()
        if cs:
            return cs[0]["name"]
        raise HTTPException(400, "还没有班级。先 photoorg init 或加人。")

    def _batch(cls: str, batch: str | None) -> str:
        cp = app_paths.class_paths(cls)
        cdb = ClassDB(cp.faces_db)
        rows = cdb.batches()
        if not rows:
            raise HTTPException(400, "这个班级还没有跑过批次")
        if batch and any(r["batch"] == batch for r in rows):
            return batch
        if batch:
            raise HTTPException(404, f"批次不存在：{batch}")
        return rows[-1]["batch"]

    def _stores(cls: str):
        cp = app_paths.class_paths(cls)
        return (
            cp,
            PersonStore(app_paths.persons_db),
            ClassDB(cp.faces_db),
            OverrideStore(cp.overrides_db),
            StateStore(cp.batch_state),
        )

    def _photo_path(hash_: str) -> Path:
        # 照片路径从库里查（批次无关，hash 全局唯一）
        for cls_row in PersonStore(app_paths.persons_db).classes():
            cdb = ClassDB(app_paths.class_paths(cls_row["name"]).faces_db)
            ph = cdb.photo(hash_)
            if ph and Path(ph["abs"]).exists():
                return Path(ph["abs"])
        raise HTTPException(404, "照片文件不在了")

    def _attachment(filename: str) -> dict:
        """HTTP 头只能 latin-1：中文文件名必须走 RFC 5987 的 filename*，
        否则 Starlette 编码响应头时直接 500（「下载报告」之前就栽在这里）。"""
        stem, dot, ext = filename.rpartition(".") if "." in filename else (filename, "", "")
        ascii_stem = "".join(ch for ch in stem if ord(ch) < 128).strip(" -_.") or "download"
        ascii_name = f"{ascii_stem}.{ext}" if dot else ascii_stem
        return {
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; '
                f"filename*=UTF-8''{quote(filename)}"
            )
        }

    def _unlink_photo(path: Path) -> None:
        """只删 photos/ 目录里的文件；数据库里记的绝对路径可能是外部路径，不碰。"""
        try:
            Path(path).resolve().relative_to(app_paths.photos.resolve())
        except ValueError:
            return
        try:
            Path(path).unlink()
        except (FileNotFoundError, IsADirectoryError):
            pass

    def _activity_batch(activity: str, when: str | None) -> str:
        """构造唯一批次名：YYYYMMDD-活动。日期为空则使用本地当天。"""
        try:
            day = date.fromisoformat((when or "").strip()) if when else date.today()
        except ValueError as exc:
            raise HTTPException(400, "活动日期格式不对，请选择日期") from exc
        name = safe_name(activity)
        base = f"{day:%Y%m%d}-{name}"
        candidate = base
        i = 2
        while (app_paths.photos / candidate).exists():
            candidate = f"{base}-{i}"
            i += 1
        return candidate

    async def _save_upload(upload: UploadFile, directory: Path, used: set[str]) -> Path | None:
        """安全地流式写上传图；浏览器的 webkitRelativePath 会以 filename 传来。"""
        original = Path(upload.filename or "photo.jpg").name
        ext = Path(original).suffix.lower()
        if ext not in IMAGE_EXTS:
            return None
        stem = safe_photo_name(Path(original).stem)
        filename = f"{stem}{ext}"
        n = 2
        while filename in used or (directory / filename).exists():
            filename = f"{stem}-{n}{ext}"
            n += 1
        used.add(filename)
        target = directory / filename
        try:
            with target.open("wb") as out:
                while chunk := await upload.read(1024 * 1024):
                    out.write(chunk)
        finally:
            await upload.close()
        if target.stat().st_size == 0:
            target.unlink(missing_ok=True)
            return None
        return target

    # ---------------- 静态页面
    # 开发期的本地浏览器很容易缓存 app.js，导致用户仍运行旧页面。
    # 显式 no-cache，使每次刷新都向本机服务确认最新版本。
    if STATIC.exists():
        class NoCacheStaticFiles(StaticFiles):
            def file_response(self, *args, **kwargs):
                response = super().file_response(*args, **kwargs)
                response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
                response.headers["Pragma"] = "no-cache"
                return response
        api.mount("/static", NoCacheStaticFiles(directory=str(STATIC)), name="static")

    @api.get("/")
    def index():
        f = STATIC / "index.html"
        if not f.exists():
            return Response("UI 缺失", status_code=500)
        return FileResponse(str(f))

    @api.get("/api/health")
    def health():
        # root 用于绿色版启动器判断「端口上的服务是不是同一个工作区」，只在本机回环地址暴露。
        return {"ok": True, "version": __version__, "root": str(app_paths.root)}

    @api.get("/api/bootstrap")
    def bootstrap(cls: str | None = Query(None, alias="class")):
        store = PersonStore(app_paths.persons_db)
        classes = []
        for c in store.classes():
            classes.append(
                {
                    "name": c["name"],
                    "n_persons": len(store.roster(int(c["id"]))),
                    "n_templates": len(store.templates(int(c["id"]))),
                }
            )
        cur = _class(cls) if classes else (cls or "")
        # 班级被删掉后，浏览器可能还带着旧班级名；回落到第一个存在的班级，
        # 否则会在磁盘上重新创建出一个空班级库。
        names = [x["name"] for x in classes]
        if names and cur not in names:
            cur = names[0]
        batches = []
        sstore = StateStore(app_paths.class_paths(cur).batch_state) if cur else None
        cdb = ClassDB(app_paths.class_paths(cur).faces_db) if cur else None
        if cdb:
            for b in cdb.batches():
                st = sstore.get(b["batch"])
                meta = batch_meta(b["batch"])
                batches.append({"batch": b["batch"], "date": meta["date"],
                                "activity": meta["activity"], "state": st["state"],
                                "label": st["label"], "n_photos": b["n_photos"],
                                "last_run": b["last_run"]})
        settings = load_settings(app_paths, cur) if cur else Settings()
        return {
            "root": str(app_paths.root),
            "class": cur,
            "classes": classes,
            "batches": batches,
            "thresholds": {"t_high": settings.t_high, "t_low": settings.t_low,
                           "margin": settings.margin},
            "model_dir": str(
                default_model_root()
                / "models"
                / str(settings.engine.get("model_name", "buffalo_l"))
            ),
            "now": now_human(),
        }

    # ---------------- 网页上传：活动照片与底库参考照片
    @api.post("/api/activity/upload")
    async def activity_upload(
        class_name: str = Form(...),
        activity: str = Form(...),
        activity_date: str | None = Form(None),
        files: list[UploadFile] = File(...),
    ):
        """创建一个新活动批次、写入上传照片，并同步跑完识别/归档。"""
        try:
            c = safe_name(class_name)
            batch = _activity_batch(activity, activity_date)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not files:
            raise HTTPException(400, "请至少选择一张活动照片")
        target = app_paths.photos / batch
        target.mkdir(parents=True, exist_ok=False)
        used: set[str] = set()
        saved: list[Path] = []
        try:
            for upload in files:
                item = await _save_upload(upload, target, used)
                if item:
                    saved.append(item)
            if not saved:
                shutil.rmtree(target, ignore_errors=True)
                raise HTTPException(400, "没有收到可用图片；支持 JPG、PNG、WEBP、BMP、TIFF")
            # 此处是同步任务；网页会处于“正在识别”状态，完成后直接返回结果，避免用户
            # 刷新页面时不知道任务跑到了哪一步。
            stats = run_batch(app_paths, c, batch)
            view = compute_batch(app_paths, c, batch)
            rebuilt = rebuild_impl(app_paths, c, batch, view=view)
            return {
                "class": c, "batch": batch, "saved": len(saved),
                "run": stats.as_dict(), "rebuild": rebuilt,
                "summary": _summary(app_paths, c, batch),
            }
        except HTTPException:
            raise
        except UserError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            # 图片已经存入活动文件夹，便于用户排查；返回明确的人话提示。
            raise HTTPException(500, f"照片已保存，但自动识别未完成：{type(exc).__name__}: {exc}") from exc

    @api.post("/api/gallery/upload")
    async def gallery_upload(
        class_name: str = Form(...),
        person_name: str = Form(""),
        person_id: int | None = Form(None),
        batch: str | None = Form(None),
        rematch_after: bool = Form(False, alias="rematch"),
        files: list[UploadFile] = File(...),
    ):
        """上传一个人的多张参考脸照片，注册为底库模板；原始临时上传会清理。

        - 已有人员可只传 ``person_id``，避免姓名拼写不一致又建出一个新的人。
        - ``rematch=true`` + ``batch`` 用于「未出现名单 → 补照片」：入库后立即重跑该批次
          的匹配与归档，补的照片能立刻把这个人从「未出现」里认回来。
        """
        try:
            c = safe_name(class_name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        store = PersonStore(app_paths.persons_db)
        if person_id is not None:
            row = store.person(int(person_id))
            if not row:
                raise HTTPException(404, "人员不存在")
            person = row["name"]
        else:
            try:
                person = safe_name(person_name)
            except ValueError as exc:
                raise HTTPException(400, "请填写人员姓名") from exc
        if not files:
            raise HTTPException(400, "请至少选择一张底库参考照片")
        tmp = app_paths.cache_dir / f"gallery-upload-{uuid4().hex}"
        tmp.mkdir(parents=True, exist_ok=False)
        used: set[str] = set()
        saved: list[Path] = []
        try:
            for upload in files:
                item = await _save_upload(upload, tmp, used)
                if item:
                    saved.append(item)
            if not saved:
                raise HTTPException(400, "没有收到可用图片；支持 JPG、PNG、WEBP、BMP、TIFF")
            result = enroll_files(app_paths, c, person, saved, source="web_upload")
            # 空结果不是 HTTP 失败：页面需要拿到每张具体被拒原因，供用户直接换图。
            result["message"] = (
                f"成功入库 {result['added']} 张"
                if result["added"] else "没有照片可入库，请按下方每张图片的拒绝原因更换图片"
            )
            klass = store.klass(c)
            person_row = store.person_by_name(person)
            payload = {
                "class": c, "person": person, "person_id": person_row["id"] if person_row else None,
                "uploaded": len(saved), "result": result,
                "template_count": len(store.templates(int(klass["id"]), int(person_row["id"]))) if klass and person_row else 0,
            }
            if rematch_after and batch and person_row:
                # 补完底库立刻重跑，页面刷新后「未出现」名单会是新的结论。
                rematch_stats = rematch(app_paths, c, batch)
                view = compute_batch(app_paths, c, batch)
                rebuild_impl(app_paths, c, batch, view=view)
                pr = next(
                    (p for p in view.persons if p.person_id == int(person_row["id"])), None
                )
                payload.update(
                    {
                        "batch": batch,
                        "rematch": rematch_stats,
                        "present_after": pr.present_count if pr else 0,
                        "absent_after": bool(pr.absent) if pr else True,
                    }
                )
            return payload
        except HTTPException:
            raise
        except UserError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @api.post("/api/gallery/upload_tree")
    async def gallery_upload_tree(
        class_name: str = Form(...),
        files: list[UploadFile] = File(...),
    ):
        """一键入库：父文件夹 → 子文件夹名即人名 → 子文件夹里的图片即该人底库照。

        浏览器会把相对路径放在 filename 里（root/张三/1.jpg），这里按第二段分组，
        只加载一次模型，避免一人一次重复初始化（每次约 8 秒）。
        """
        try:
            c = safe_name(class_name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not files:
            raise HTTPException(400, "请选择包含人名子文件夹的整个文件夹")

        groups: dict[str, list[UploadFile]] = {}
        skipped_files = 0
        for upload in files:
            parts = Path(upload.filename or "").parts
            if len(parts) < 3:  # 直接放在父文件夹里的图片没有归属，跳过
                skipped_files += 1
                continue
            try:
                person = safe_name(parts[1])
            except ValueError:
                skipped_files += 1
                continue
            groups.setdefault(person, []).append(upload)
        if not groups:
            raise HTTPException(400, "没有找到「人名/图片」结构，请选择包含子文件夹的父文件夹")

        tmp_root = app_paths.cache_dir / f"gallery-tree-{uuid4().hex}"
        tmp_root.mkdir(parents=True, exist_ok=False)
        settings = load_settings(app_paths, c)
        engine = FaceEngine(settings)  # 整批只加载一次
        results: list[dict] = []
        try:
            for person, uploads in groups.items():
                pdir = tmp_root / person
                pdir.mkdir(parents=True, exist_ok=True)
                used: set[str] = set()
                saved: list[Path] = []
                for upload in uploads:
                    item = await _save_upload(upload, pdir, used)
                    if item:
                        saved.append(item)
                if not saved:
                    results.append({"person": person, "added": 0, "rejected": [],
                                    "n_rejected": 0, "skipped": len(uploads)})
                    continue
                try:
                    r = enroll_files(app_paths, c, person, saved, engine=engine,
                                     settings=settings, source="web_tree")
                except UserError as exc:
                    results.append({"person": person, "added": 0, "rejected": [str(exc)],
                                    "n_rejected": 1, "skipped": 0})
                    continue
                rejected = list(r.get("rejected") or [])
                results.append({
                    "person": person,
                    "added": int(r.get("added") or 0),
                    "rejected": rejected[:3],
                    "n_rejected": len(rejected),
                    "skipped": len(uploads) - len(saved),
                })
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)

        results.sort(key=lambda x: x["person"])
        return {
            "class": c,
            "persons": results,
            "n_persons": len(results),
            "n_added": sum(r["added"] for r in results),
            "n_rejected": sum(r["n_rejected"] for r in results),
            "skipped_files": skipped_files,
        }

    @api.post("/api/person/photos/add")
    async def person_photo_add(
        class_name: str = Form(...),
        batch: str = Form(...),
        person_id: int = Form(...),
        files: list[UploadFile] = File(...),
    ):
        """把「这次活动里拍到某个人」的照片补进当前活动，并直接记为出现。

        与底库上传不同：照片只进入 photos/<批次>/，参与本活动的归档与出勤统计，
        不会自动变成底库模板（活动抓拍通常达不到底库质量要求）。需要时可在照片
        详情里对单张脸手动「加为模板」。
        """
        try:
            c = safe_name(class_name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        store = PersonStore(app_paths.persons_db)
        person = store.person(int(person_id))
        if not person:
            raise HTTPException(404, "人员不存在")
        klass = store.klass(c)
        if klass and int(person_id) not in {int(r["id"]) for r in store.roster(int(klass["id"]))}:
            raise HTTPException(400, f"「{person['name']}」不在这个班的名单里")
        target = app_paths.photos / batch
        if not target.exists():
            raise HTTPException(404, f"活动「{batch}」的照片文件夹不存在")
        if not files:
            raise HTTPException(400, "请至少选择一张照片")

        cdb = ClassDB(app_paths.class_paths(c).faces_db)
        used: set[str] = set()
        saved: list[Path] = []
        for upload in files:
            item = await _save_upload(upload, target, used)
            if item:
                saved.append(item)
        if not saved:
            raise HTTPException(400, "没有收到可用图片；支持 JPG、PNG、WEBP、BMP、TIFF")

        new_files: list[Path] = []
        reuse: list[str] = []
        skipped: list[str] = []
        seen: set[str] = set()
        for path in saved:
            h = sha256_file(path)
            if h in seen:
                path.unlink(missing_ok=True)
                continue
            seen.add(h)
            row = cdb.photo(h)
            if row is None:
                new_files.append(path)
                continue
            # 库里已有同一张照片：不重复占空间，直接复用它的识别结果
            path.unlink(missing_ok=True)
            if row["batch"] == batch:
                reuse.append(h)
            else:
                skipped.append(f"已在批次「{row['batch']}」")
        if not new_files and not reuse:
            raise HTTPException(400, "这些照片已经在别的活动里了：" + "；".join(skipped[:3]))

        if new_files:
            try:
                run_batch(app_paths, c, batch)
            except UserError as exc:
                raise HTTPException(400, str(exc)) from exc
        hashes = reuse + [sha256_file(p) for p in new_files]

        ostore = OverrideStore(app_paths.class_paths(c).overrides_db)
        no_face = 0
        for h in hashes:
            faces = cdb.faces(h)
            if not faces:
                no_face += 1
                continue
            main = max(
                faces,
                key=lambda f: (f["bbox"][2] - f["bbox"][0]) * (f["bbox"][3] - f["bbox"][1]),
            )
            ostore.add(
                "photo_person", "present", photo_hash=h, person_id=int(person_id),
                value="present", source="web_upload", operator="ui",
                reason=f"结果审查：上传活动照片记为「{person['name']}」出现", batch=batch,
            )
            ostore.add(
                "face_assign", "assign", photo_hash=h, person_id=int(person_id),
                face_uid=face_uid(h, int(main["face_idx"])), value=str(person_id),
                source="web_upload", operator="ui", reason="结果审查：上传活动照片", batch=batch,
            )

        sstore = StateStore(app_paths.class_paths(c).batch_state)
        sstore.note_edit(
            batch, by="ui",
            note=f"补充活动照片并记为「{person['name']}」出现（{len(hashes)} 张）",
        )
        view = compute_batch(app_paths, c, batch)
        rebuilt = rebuild_impl(app_paths, c, batch, view=view)
        pr = next((p for p in view.persons if p.person_id == int(person_id)), None)
        return {
            "class": c,
            "batch": batch,
            "person": person["name"],
            "photos_added": len(hashes),
            "photos_skipped": len(skipped),
            "no_face": no_face,
            "present_after": pr.present_count if pr else 0,
            "state": sstore.get(batch),
            "rebuild": rebuilt,
        }

    @api.get("/api/batch")
    def batch_detail(batch: str | None = None, cls: str | None = Query(None, alias="class")):
        c = _class(cls)
        b = _batch(c, batch)
        settings = load_settings(app_paths, c)
        view = compute_batch(app_paths, c, b, settings)
        # 照片按内容唯一：同一张照片被传到别的批次后，本批次目录里还留着原文件，
        # 但结论已经归到另一个批次。这里数一下，避免用户以为「界面丢了照片」。
        batch_dir = app_paths.photos / b
        known = {ph["rel"] for ph in view.photos}
        orphans = 0
        if batch_dir.exists():
            for p in iter_images(batch_dir):
                if str(p.relative_to(batch_dir)) not in known:
                    orphans += 1
        persons = [
            {
                "person_id": p.person_id,
                "name": p.name,
                "count": p.present_count,
                "auto": p.auto_count,
                "manual": p.manual_count,
                "review": len(p.review_photos),
                "absent": p.absent,
                "absent_confirmed": p.absent_confirmed,
                "n_templates": p.n_templates,
                "flagged": p.flagged,
                "evidence": p.evidence,
                "photos": [
                    {"photo_hash": x.photo_hash, "photo_name": x.photo_name,
                     "status": x.status, "source": x.source, "score": round(x.score, 3),
                     "manual": x.manual, "reason": x.reason, "override_id": x.override_id}
                    for x in p.photos
                ],
                "review_photos": [
                    {"photo_hash": x.photo_hash, "photo_name": x.photo_name,
                     "score": round(x.score, 3)}
                    for x in p.review_photos
                ],
            }
            for p in view.persons
        ]
        return {
            "batch": b,
            "class": c,
            "meta": batch_meta(b),
            "state": view.state,
            "stats": view.stats,
            "persons": persons,
            "review_count": len(view.review_items),
            "unknown": view.unknown_faces[:60],
            "draft_banner": view.draft_banner,
            "can_publish": view.can_publish,
            "review_ready": view.review_ready,
            "absent_ready": view.absent_ready,
            "thresholds": {"t_high": view.settings.t_high, "t_low": view.settings.t_low,
                           "margin": view.settings.margin},
            "orphan_files": orphans,
            "generated_at": now_human(),
        }

    # ---------------- 图像
    @api.get("/api/image/{hash_}")
    def image(hash_: str, max: int = 1400):
        p = _photo_path(hash_)
        img = resize_max(imread(p), max_side=max)
        return Response(jpeg_bytes(img, 88), media_type="image/jpeg")

    @api.get("/api/crop/{hash_}/{idx}")
    def crop(hash_: str, idx: int, size: int = 200, pad: float = 0.28):
        cls = None
        img = None
        for cls_row in PersonStore(app_paths.persons_db).classes():
            cdb = ClassDB(app_paths.class_paths(cls_row["name"]).faces_db)
            ph = cdb.photo(hash_)
            if not ph:
                continue
            faces = {f["face_idx"]: f for f in cdb.faces(hash_)}
            f = faces.get(int(idx))
            if not f:
                raise HTTPException(404, "这张脸不在了")
            img = imread(Path(ph["abs"]))
            patch = crop_box(img, f["bbox"], pad=pad)
            h, w = patch.shape[:2]
            if max(h, w) > size:
                import cv2
                s = size / max(h, w)
                patch = cv2.resize(patch, (max(1, int(w * s)), max(1, int(h * s))),
                                   interpolation=cv2.INTER_AREA)
            return Response(jpeg_bytes(patch, 90), media_type="image/jpeg")
        raise HTTPException(404, "照片不在了")

    # ---------------- 灰区复核
    @api.get("/api/review")
    def review_list(batch: str | None = None, cls: str | None = Query(None, alias="class")):
        c = _class(cls)
        b = _batch(c, batch)
        view = compute_batch(app_paths, c, b)
        items = list(view.review_items)
        sstore = StateStore(app_paths.class_paths(c).batch_state)
        return {
            "batch": b,
            "class": c,
            "items": items,
            "remaining": len(items),
            "state": sstore.get(b),
            "fallback_persons": [{"person_id": p.person_id, "name": p.name} for p in view.persons],
            "thresholds": {"t_high": view.settings.t_high, "t_low": view.settings.t_low},
            "templates": _template_counts(app_paths, c),
        }

    @api.post("/api/review/decide")
    def review_decide(req: DecideReq):
        # 班级从批次反查，避免前端每个请求都要传 class
        c = _class_for_batch(app_paths, req.batch)
        if not c:
            raise HTTPException(404, "这个批次不属于任何班级")
        try:
            r = decide_face(
                app_paths, c, req.batch, req.photo_hash, req.face_idx, req.action,
                person_id=req.person_id, new_name=req.new_name,
                operator=req.operator or "ui", reason=req.reason, source="ui",
            )
        except UserError as e:
            raise HTTPException(400, str(e))
        rr = rebuild_impl(app_paths, c, req.batch)
        return {"result": r, "rebuild": rr, "remaining": _remaining(app_paths, c, req.batch)}

    # ---------------- 照片×人
    @api.post("/api/pair")
    def pair_set(req: PairReq):
        c = _class_for_batch(app_paths, req.batch) or _class(None)
        try:
            r = set_photo_person(app_paths, c, req.batch, req.photo_hash, req.person_id,
                                 req.action, source="ui", operator=req.operator or "ui",
                                 reason=req.reason)
        except UserError as e:
            raise HTTPException(400, str(e))
        rr = rebuild_impl(app_paths, c, req.batch)
        return {"result": r, "rebuild": rr, "summary": _summary(app_paths, c, req.batch)}

    @api.post("/api/pair/clear")
    def pair_clear(req: PairReq):
        c = _class_for_batch(app_paths, req.batch) or _class(None)
        r = clear_photo_person(app_paths, c, req.batch, req.photo_hash, req.person_id)
        rr = rebuild_impl(app_paths, c, req.batch)
        return {"result": r, "rebuild": rr, "summary": _summary(app_paths, c, req.batch)}

    @api.post("/api/undo")
    def undo(req: BatchReq):
        c = _class_for_batch(app_paths, req.batch) or _class(None)
        r = undo_impl(app_paths, c, batch=req.batch)
        rr = rebuild_impl(app_paths, c, req.batch)
        return {"result": r, "rebuild": rr, "summary": _summary(app_paths, c, req.batch)}

    @api.get("/api/photo/{hash_}")
    def photo_detail(hash_: str, cls: str | None = Query(None, alias="class")):
        c = _class(cls)
        cp = app_paths.class_paths(c)
        cdb = ClassDB(cp.faces_db)
        ph = cdb.photo(hash_)
        if not ph:
            raise HTTPException(404, "照片记录不存在")
        view = compute_batch(app_paths, c, ph["batch"])
        present = [
            {"person_id": p.person_id, "name": p.name}
            for p in view.persons
            for x in p.photos
            if x.photo_hash == hash_
        ]
        return {
            "hash": hash_,
            "name": ph["name"],
            "width": ph["width"],
            "height": ph["height"],
            "faces": photo_faces_detail(app_paths, c, hash_),
            "present": present,
        }

    @api.get("/api/audit")
    def audit(batch: str | None = None, cls: str | None = Query(None, alias="class"), limit: int = 200):
        c = _class(cls)
        return {"items": audit_log(app_paths, c, batch=batch, limit=limit)}

    # ---------------- 底库
    @api.get("/api/gallery")
    def gallery(person_id: int, cls: str | None = Query(None, alias="class")):
        c = _class(cls)
        store = PersonStore(app_paths.persons_db)
        cp = app_paths.class_paths(c)
        klass = store.klass(c)
        if not klass:
            raise HTTPException(404, "班级不存在")
        rows = store.templates(int(klass["id"]), person_id, active_only=False)
        out = []
        for r in rows:
            p = cp.dir / r["path"] if r["path"] else None
            out.append({
                "id": r["id"],
                "active": bool(r["active"]),
                "quality": round(float(r["quality"]), 3),
                "yaw": round(float(r["yaw"]), 1),
                "source": r["source"],
                "created_at": r["created_at"],
                "photo_hash": r["photo_hash"],
                "face_idx": r["face_idx"],
                "url": f"/api/gallery/img/{r['id']}",
            })
        return {"person_id": person_id, "templates": out,
                "health": [h for h in gallery_health(app_paths, c) if h["person_id"] == person_id]}

    @api.get("/api/gallery/img/{tid}")
    def gallery_img(tid: int, cls: str | None = Query(None, alias="class")):
        c = _class(cls)
        store = PersonStore(app_paths.persons_db)
        row = store.template(tid)
        if not row:
            raise HTTPException(404, "模板不存在")
        cp = app_paths.class_paths(c)
        p = cp.dir / row["path"]
        if not p.exists():
            raise HTTPException(404, "模板文件不见了")
        return Response(p.read_bytes(), media_type="image/jpeg")

    @api.post("/api/gallery/add")
    def gallery_add(req: GalleryAddReq):
        c = _class_for_batch(app_paths, req.batch) or _class(None)
        try:
            r = add_template_from_face(app_paths, c, req.person_id, req.photo_hash,
                                       req.face_idx, source="manual", force=req.force)
        except UserError as e:
            raise HTTPException(400, str(e))
        return {"result": r}

    @api.post("/api/gallery/remove")
    def gallery_remove(template_id: int, cls: str | None = Query(None, alias="class")):
        c = _class(cls)
        store = PersonStore(app_paths.persons_db)
        row = store.template(template_id)
        if not row:
            raise HTTPException(404, "模板不存在")
        store.delete_template(template_id)
        cp = app_paths.class_paths(c)
        if row["path"]:
            p = cp.dir / row["path"]
            if p.exists():
                p.unlink()
        return {"removed": template_id}

    @api.get("/api/gallery/health")
    def gallery_health_api(cls: str | None = Query(None, alias="class")):
        c = _class(cls)
        rows = gallery_health(app_paths, c)
        view = None
        try:
            b = _batch(c, None)
            view = compute_batch(app_paths, c, b)
        except HTTPException:
            pass
        if view:
            stat = {p.person_id: p for p in view.persons}
            for r in rows:
                p = stat.get(r["person_id"])
                if p:
                    r["count"] = p.present_count
                    r["review"] = len(p.review_photos)
        return {"class": c, "persons": rows}

    # ---------------- 名单
    @api.post("/api/person/add")
    def person_add(req: PersonReq):
        c = _class(req.class_name or None)
        store = PersonStore(app_paths.persons_db)
        class_id = store.upsert_class(c)  # 返回 class_id（int），不是 dict
        pid = store.upsert_person(req.name)
        store.add_member(int(class_id), pid)
        try:
            rematch(app_paths, c, _batch(c, None))
        except HTTPException:
            pass  # 还没有任何批次时，加人不该失败
        return {"person_id": pid, "name": req.name}

    @api.post("/api/person/remove")
    def person_remove(person_id: int, cls: str | None = Query(None, alias="class")):
        c = _class(cls)
        delete_person_with_gallery(app_paths, c, person_id)
        try:
            rematch(app_paths, c, _batch(c, None))
        except HTTPException:
            pass
        return {"removed": person_id}

    @api.post("/api/person/rename")
    def person_rename(req: RenameReq):
        """改人名：报告、归档目录、底库都按 person_id 关联，改完只需重建 out/。"""
        c = _class(req.class_name or None)
        store = PersonStore(app_paths.persons_db)
        row = store.person(req.person_id)
        if not row:
            raise HTTPException(404, "人员不存在")
        new = (req.new_name or "").strip()
        if not new:
            raise HTTPException(400, "新名字不能为空")
        if new == row["name"]:
            return {"person_id": req.person_id, "name": new, "rebuilt": 0, "unchanged": True}
        other = store.person_by_name(new)
        if other and int(other["id"]) != int(req.person_id):
            raise HTTPException(400, f"已经有叫「{new}」的人，请换一个名字")
        store.rename_person(req.person_id, new)
        rebuilt = 0
        for b in ClassDB(app_paths.class_paths(c).faces_db).batches():
            try:
                rebuild_impl(app_paths, c, b["batch"])
                rebuilt += 1
            except Exception:  # 归档重建失败不影响改名本身
                pass
        return {"person_id": req.person_id, "name": new, "rebuilt": rebuilt, "unchanged": False}

    # ---------------- 状态 / 发布
    @api.post("/api/absent/confirm")
    def absent_confirm(req: ConfirmReq):
        c = _class_for_batch(app_paths, req.batch) or _class(None)
        sstore = StateStore(app_paths.class_paths(c).batch_state)
        if req.confirmed:
            sstore.confirm_absent(req.batch, req.person_id, by=req.by or "ui")
        else:
            sstore.unconfirm_absent(req.batch, req.person_id)
        view = compute_batch(app_paths, c, req.batch)
        return {"state": sstore.get(req.batch), "can_publish": view.can_publish,
                "absent_ready": view.absent_ready, "review_ready": view.review_ready}

    @api.post("/api/publish")
    def publish(req: PublishReq):
        c = _class_for_batch(app_paths, req.batch) or _class(None)
        if not req.by.strip():
            raise HTTPException(400, "发布需要填确认人（报告页眉会印出来）")
        view = compute_batch(app_paths, c, req.batch)
        if view.review_items:
            raise HTTPException(400, f"还有 {len(view.review_items)} 条灰区未确认，禁止发布正式名单")
        unconf = [p.name for p in view.persons if p.absent and not p.absent_confirmed]
        if unconf:
            raise HTTPException(400, f"还有 {len(unconf)} 人「未出现」没逐人确认：{'、'.join(unconf[:6])}")
        sstore = StateStore(app_paths.class_paths(c).batch_state)
        sstore.set_state(req.batch, REVIEWED, by=req.by, note="UI 确认")
        sstore.set_state(req.batch, PUBLISHED, by=req.by, note="UI 发布")
        rr = rebuild_impl(app_paths, c, req.batch)
        return {"state": sstore.get(req.batch), "rebuild": rr}

    @api.post("/api/unpublish")
    def unpublish(req: BatchReq):
        c = _class_for_batch(app_paths, req.batch) or _class(None)
        sstore = StateStore(app_paths.class_paths(c).batch_state)
        sstore.set_state(req.batch, REVIEWED, by="ui", note="撤回发布")
        rebuild_impl(app_paths, c, req.batch)
        return {"state": sstore.get(req.batch)}

    # ---------------- 归档 / 校准 / 冷启动
    @api.post("/api/rebuild")
    def rebuild(req: RebuildReq):
        c = _class_for_batch(app_paths, req.batch) or _class(None)
        return rebuild_impl(app_paths, c, req.batch, dry_run=req.dry_run)

    @api.post("/api/adopt")
    def adopt(req: BatchReq):
        c = _class_for_batch(app_paths, req.batch) or _class(None)
        r = adopt_impl(app_paths, c, req.batch, operator="ui")
        rr = rebuild_impl(app_paths, c, req.batch)
        return {"result": r, "rebuild": rr}

    @api.get("/api/diff")
    def diff(batch: str | None = None, cls: str | None = Query(None, alias="class")):
        c = _class(cls)
        b = _batch(c, batch)
        view = compute_batch(app_paths, c, b)
        return {"batch": b, "diff": diff_auto_vs_manual(view),
                "summary": _summary(app_paths, c, b)}

    @api.post("/api/calibrate")
    def calibrate(req: BatchReq):
        c = _class_for_batch(app_paths, req.batch) or _class(None)
        try:
            return suggest_thresholds(app_paths, c)
        except UserError as e:
            raise HTTPException(400, str(e))

    @api.post("/api/calibrate/apply")
    def calibrate_apply(req: CalibrateApplyReq):
        c = _class_for_batch(app_paths, req.batch) or _class(None)
        try:
            r = apply_thresholds(app_paths, c, req.t_high, req.t_low, req.margin, by=req.by)
        except UserError as e:
            raise HTTPException(400, str(e))
        return r

    @api.get("/api/clusters")
    def clusters(batch: str | None = None, threshold: float = 0.55, min_size: int = 2,
                 only_unknown: bool = False, cls: str | None = Query(None, alias="class")):
        c = _class(cls)
        b = _batch(c, batch)
        try:
            cs = cluster_faces(app_paths, c, b, threshold=threshold, min_size=min_size,
                               only_unknown=only_unknown)
        except UserError as e:
            raise HTTPException(400, str(e))
        return {
            "batch": b,
            "clusters": [
                {
                    "cid": x.cid,
                    "size": x.size,
                    "members": [
                        {**m, "crop": f"/api/crop/{m['photo_hash']}/{m['face_idx']}"}
                        for m in x.members[:24]
                    ],
                }
                for x in cs
            ],
        }

    @api.post("/api/clusters/accept")
    def clusters_accept(req: ClusterAcceptReq):
        c = _class_for_batch(app_paths, req.batch) or _class(None)
        try:
            r = accept_cluster(app_paths, c, req.batch, req.name, req.members,
                               operator=req.operator or "ui")
        except UserError as e:
            raise HTTPException(400, str(e))
        rematch(app_paths, c, req.batch)
        rr = rebuild_impl(app_paths, c, req.batch)
        return {"result": r, "rebuild": rr}

    # ---------------- 导出
    @api.get("/api/export/report.md")
    def export_md(batch: str | None = None, cls: str | None = Query(None, alias="class")):
        c = _class(cls)
        b = _batch(c, batch)
        view = compute_batch(app_paths, c, b)
        return Response(build_markdown(view), media_type="text/markdown; charset=utf-8",
                        headers=_attachment(f"report-{b}.md"))

    @api.get("/api/export/report.csv")
    def export_csv(batch: str | None = None, cls: str | None = Query(None, alias="class")):
        c = _class(cls)
        b = _batch(c, batch)
        view = compute_batch(app_paths, c, b)
        return Response(build_csv(view), media_type="text/csv; charset=utf-8",
                        headers=_attachment(f"report-{b}.csv"))

    @api.get("/api/export/overrides.csv")
    def export_overrides(batch: str | None = None, cls: str | None = Query(None, alias="class")):
        c = _class(cls)
        b = _batch(c, batch)
        return Response(export_overrides_csv(app_paths, c, b), media_type="text/csv; charset=utf-8",
                        headers=_attachment(f"overrides-{b}.csv"))

    # ---------------- 年度统计（活动 × 人员）
    @api.get("/api/matrix")
    def matrix(
        cls: str | None = Query(None, alias="class"),
        date_from: str = Query("", alias="from"),
        date_to: str = Query("", alias="to"),
    ):
        c = _class(cls)
        try:
            return class_matrix(app_paths, c, date_from, date_to)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @api.get("/api/export/matrix.csv")
    def export_matrix(
        cls: str | None = Query(None, alias="class"),
        date_from: str = Query("", alias="from"),
        date_to: str = Query("", alias="to"),
    ):
        c = _class(cls)
        try:
            data = class_matrix(app_paths, c, date_from, date_to)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return Response(
            matrix_csv(data),
            media_type="text/csv; charset=utf-8",
            headers=_attachment(f"出勤统计-{c}.csv"),
        )

    @api.get("/api/person/photos")
    def person_photo_list(
        person_id: int,
        cls: str | None = Query(None, alias="class"),
        date_from: str = Query("", alias="from"),
        date_to: str = Query("", alias="to"),
    ):
        c = _class(cls)
        try:
            return person_photos(app_paths, c, person_id, date_from, date_to)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    # ---------------- 活动照片（查看 / 追加 / 删除）
    @api.get("/api/batch/photos")
    def batch_photo_list(batch: str | None = None, cls: str | None = Query(None, alias="class")):
        c = _class(cls)
        b = _batch(c, batch)
        cdb = ClassDB(app_paths.class_paths(c).faces_db)
        rows = [
            {
                "photo_hash": ph["hash"],
                "name": ph["name"],
                "status": ph["status"],
                "n_faces": int(ph["n_faces"] or 0),
                "width": int(ph["width"] or 0),
                "height": int(ph["height"] or 0),
                "error": ph["error"] or "",
            }
            for ph in cdb.photos(batch=b)
        ]
        return {"batch": b, "class": c, "count": len(rows), "photos": rows}

    @api.post("/api/batch/photos")
    async def batch_photo_add(
        class_name: str = Form(...),
        batch: str = Form(...),
        files: list[UploadFile] = File(...),
    ):
        """往已有活动里追加照片，然后重跑识别、匹配与归档。"""
        try:
            c = safe_name(class_name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        target = app_paths.photos / batch
        if not target.exists():
            raise HTTPException(404, f"活动「{batch}」的照片文件夹不存在")
        used: set[str] = set()
        saved: list[Path] = []
        for upload in files:
            item = await _save_upload(upload, target, used)
            if item:
                saved.append(item)
        if not saved:
            raise HTTPException(400, "没有收到可用图片；支持 JPG、PNG、WEBP、BMP、TIFF")
        try:
            stats = run_batch(app_paths, c, batch)
            view = compute_batch(app_paths, c, batch)
            rebuilt = rebuild_impl(app_paths, c, batch, view=view)
        except UserError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "class": c, "batch": batch, "saved": len(saved),
            "run": stats.as_dict(), "rebuild": rebuilt,
            "summary": _summary(app_paths, c, batch),
        }

    @api.post("/api/photo/delete")
    def photo_delete(req: PhotoDeleteReq):
        c = _class_for_batch(app_paths, req.batch) or _class(None)
        cdb = ClassDB(app_paths.class_paths(c).faces_db)
        ph = cdb.photo(req.photo_hash)
        if not ph:
            raise HTTPException(404, "照片不存在")
        if ph["batch"] != req.batch:
            raise HTTPException(400, "这张照片不属于该活动")
        _unlink_photo(Path(ph["abs"]))
        cdb.delete_photo(req.photo_hash)
        rematch(app_paths, c, req.batch)
        rebuild_impl(app_paths, c, req.batch)
        return {"deleted": req.photo_hash, "batch": req.batch,
                "summary": _summary(app_paths, c, req.batch)}

    @api.post("/api/batch/delete")
    def batch_delete(req: BatchDeleteReq):
        c = _class_for_batch(app_paths, req.batch) or _class(None)
        cdb = ClassDB(app_paths.class_paths(c).faces_db)
        photos = cdb.photos(batch=req.batch)
        if not photos:
            raise HTTPException(404, "活动不存在或没有照片")
        if req.delete_photos:
            for ph in photos:
                _unlink_photo(Path(ph["abs"]))
        n = cdb.delete_batch(req.batch)
        StateStore(app_paths.class_paths(c).batch_state).remove(req.batch)
        shutil.rmtree(app_paths.out / req.batch, ignore_errors=True)
        if req.delete_photos:
            shutil.rmtree(app_paths.photos / req.batch, ignore_errors=True)
        return {"batch": req.batch, "deleted_photos": n, "photos_dir_removed": req.delete_photos}

    @api.post("/api/class/delete")
    def class_delete(req: ClassDeleteReq):
        try:
            name = safe_name(req.class_name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        store = PersonStore(app_paths.persons_db)
        if not store.klass(name):
            raise HTTPException(404, "班级不存在")
        cdb = ClassDB(app_paths.class_paths(name).faces_db)
        batches = [b["batch"] for b in cdb.batches()]
        for b in batches:
            if req.delete_photos:
                for ph in cdb.photos(batch=b):
                    _unlink_photo(Path(ph["abs"]))
                shutil.rmtree(app_paths.photos / b, ignore_errors=True)
            cdb.delete_batch(b)
            shutil.rmtree(app_paths.out / b, ignore_errors=True)
        store.delete_class(name)
        shutil.rmtree(app_paths.class_paths(name).dir, ignore_errors=True)
        # 默认班级被删掉就换一个，避免下次打开网页指向一个不存在的班级
        cfg = app_paths.global_config
        if cfg.exists():
            try:
                data = json.loads(cfg.read_text("utf-8"))
            except Exception:
                data = {}
            if data.get("default_class") == name:
                left = [x["name"] for x in store.classes()]
                data["default_class"] = left[0] if left else ""
                cfg.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")
        return {"deleted": name, "batches": len(batches), "photos_deleted": req.delete_photos}

    @api.exception_handler(UserError)
    def user_error_handler(request: Request, exc: UserError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    return api


# --------------------------------------------------------------- 小工具


def _class_for_batch(app: AppPaths, batch: str) -> str | None:
    if not app.classes_dir.exists():
        return None
    for d in sorted(app.classes_dir.iterdir()):
        if not d.is_dir():
            continue
        cdb = ClassDB(d / "faces.db")
        if cdb.photos(batch=batch)[:1]:
            return d.name
    return None


def _remaining(app: AppPaths, cls: str, batch: str) -> int:
    return len(compute_batch(app, cls, batch).review_items)


def _summary(app: AppPaths, cls: str, batch: str) -> dict:
    view = compute_batch(app, cls, batch)
    return {
        "present": view.n_present_pairs,
        "review": len(view.review_items),
        "absent": len(view.absent_persons),
        "manual": view.stats.get("n_manual", 0),
        "can_publish": view.can_publish,
        "state": view.state,
        "draft_banner": view.draft_banner,
    }


def _template_counts(app: AppPaths, cls: str) -> dict:
    store = PersonStore(app.persons_db)
    klass = store.klass(cls)
    if not klass:
        return {}
    return {str(r["id"]): len(store.templates(int(klass["id"]), int(r["id"])))
            for r in store.roster(int(klass["id"]))}
