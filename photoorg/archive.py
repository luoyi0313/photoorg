"""归档：out/<批次>/ 每次全量重建（§8.5）。

最容易出的 bug 是「改判后旧文件残留」——所以这里永远重建，不做增量打补丁。
重建走临时目录 + 原子换名，中途失败不会留下半个目录。
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from .config import AppPaths
from .db import ClassDB, OverrideStore, PersonStore
from .imaging import iter_images, link_or_copy, safe_photo_name
from .pipeline import UserError, load_settings
from .report import build_csv, build_markdown
from .results import BatchView, compute_batch
from .state import StateStore

REVIEW_DIR = "_review"
DO_NOT_EDIT = (
    "此目录是派生产物，每次运行会自动重建，请不要手动编辑。\n"
    "想改结论：photoorg review（或改 CSV 后 photoorg fix --csv）。\n"
    "想手动拖文件：拖完跑 photoorg adopt --batch <批次>，系统会把你的调整吸收成人工修正。\n"
)


def out_dir(app: AppPaths, batch: str) -> Path:
    return app.out / batch


def _unique_filename(used: set[str], name: str, photo_hash: str) -> str:
    name = safe_photo_name(name)
    if name not in used:
        used.add(name)
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    cand = f"{stem}~{photo_hash[:6]}{('.' + ext) if ext else ''}"
    i = 2
    while cand in used:
        cand = f"{stem}~{photo_hash[:6]}-{i}{('.' + ext) if ext else ''}"
        i += 1
    used.add(cand)
    return cand


def plan_links(view: BatchView) -> dict[str, list[tuple[Path, str]]]:
    """{目录名: [(源文件, 目标文件名)]}，人名目录 + _review 一起算。"""
    by_hash = {ph["hash"]: ph for ph in view.photos}
    plan: dict[str, list[tuple[Path, str]]] = {}
    for p in view.persons:
        if not p.photos:
            continue
        used: set[str] = set()
        items = []
        for pr in p.photos:
            ph = by_hash.get(pr.photo_hash)
            if not ph or ph["status"] == "error":
                continue
            items.append((Path(ph["abs"]), _unique_filename(used, ph["name"], ph["hash"])))
        if items:
            plan[safe_photo_name(p.name)] = items

    used_review: set[str] = set()
    review_items = []
    for p in view.persons:
        for pr in p.review_photos:
            ph = by_hash.get(pr.photo_hash)
            if not ph or ph["status"] == "error":
                continue
            base = f"{safe_photo_name(p.name)}__{ph['name']}"
            review_items.append((Path(ph["abs"]), _unique_filename(used_review, base, ph["hash"])))
    if review_items:
        plan[REVIEW_DIR] = review_items
    return plan


def expected_names(plan: dict[str, list[tuple[Path, str]]]) -> set[tuple[str, str]]:
    return {(d, name) for d, items in plan.items() for _, name in items}


def current_names(root: Path, links_only: bool = False) -> set[tuple[str, str]]:
    root = Path(root)
    if not root.is_dir():
        return set()
    out: set[tuple[str, str]] = set()
    for sub in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        for f in sub.iterdir():
            if f.is_file() and not f.name.startswith("."):
                out.add((sub.name, f.name))
    return out


def rebuild(
    app: AppPaths,
    class_name: str,
    batch: str,
    *,
    view: BatchView | None = None,
    dry_run: bool = False,
) -> dict:
    settings = load_settings(app, class_name)
    view = view or compute_batch(app, class_name, batch, settings)
    plan = plan_links(view)
    target = out_dir(app, batch)
    before = current_names(target)
    after = expected_names(plan)
    added = sorted(after - before)
    removed = sorted(before - after)

    t0 = time.perf_counter()
    if dry_run:
        return {
            "batch": batch,
            "dry_run": True,
            "n_add": len(added),
            "n_remove": len(removed),
            "added": added[:50],
            "removed": removed[:50],
            "target": str(target),
        }

    tmp = target.parent / f".tmp-{safe_photo_name(batch)}-{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    n_link = n_copy = 0
    try:
        for dirname, items in plan.items():
            d = tmp / dirname
            d.mkdir(parents=True, exist_ok=True)
            for src, name in items:
                try:
                    how = link_or_copy(src, d / name)
                    n_link += how == "link"
                    n_copy += how == "copy"
                except OSError as e:
                    view.stats.setdefault("link_errors", []).append(f"{name}: {e}")
        # 报告与说明
        (tmp / "report.md").write_text(build_markdown(view), "utf-8")
        (tmp / "report.csv").write_text(build_csv(view), "utf-8")
        (tmp / "请注意_此目录会自动重建.txt").write_text(DO_NOT_EDIT, "utf-8")
        (tmp / REVIEW_DIR / "review.json").parent.mkdir(parents=True, exist_ok=True)
        (tmp / REVIEW_DIR / "review.json").write_text(
            json.dumps(view.review_items, ensure_ascii=False, indent=2), "utf-8"
        )
        if target.exists():
            shutil.rmtree(target)
        tmp.replace(target)
    except BaseException:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        raise

    elapsed = time.perf_counter() - t0
    # 报告另存一份到班级目录，方便长期归档（out/ 会被重建）
    cp = app.class_paths(class_name)
    cp.reports.mkdir(parents=True, exist_ok=True)
    (cp.reports / f"{safe_photo_name(batch)}-report.md").write_text(build_markdown(view), "utf-8")
    (cp.reports / f"{safe_photo_name(batch)}-report.csv").write_text(build_csv(view), "utf-8")

    return {
        "batch": batch,
        "dry_run": False,
        "n_link": n_link,
        "n_copy": n_copy,
        "n_add": len(added),
        "n_remove": len(removed),
        "elapsed": round(elapsed, 3),
        "target": str(target),
    }


def adopt(app: AppPaths, class_name: str, batch: str, operator: str = "adopt") -> dict:
    """扫描 out/ 与数据库的差异，把「手动拖文件」反写成 overrides（source=adopt）。"""
    cp = app.class_paths(class_name)
    cdb = ClassDB(cp.faces_db)
    ostore = OverrideStore(cp.overrides_db)
    sstore = StateStore(cp.batch_state)

    photos = {p["name"]: p for p in cdb.photos(batch=batch) if p["status"] == "ok"}
    target = out_dir(app, batch)
    if not target.exists():
        raise UserError(f"{target} 不存在，先跑一次 photoorg rebuild --batch {batch}")

    # 现状：目录名 = 人名
    actual: dict[str, set[str]] = {}
    for sub in (p for p in target.iterdir() if p.is_dir() and not p.name.startswith(".")):
        if sub.name == REVIEW_DIR:
            continue
        actual[sub.name] = {f.name for f in sub.iterdir() if f.is_file() and not f.name.startswith(".")}

    tmp = compute_batch(app, class_name, batch)
    expected: dict[str, set[str]] = {}
    for p in tmp.persons:
        names = {pr.photo_name for pr in p.photos}
        if names:
            expected[p.name] = names

    person_by_name = {p.name: p.person_id for p in tmp.persons}
    added = removed = 0
    details: list[str] = []
    for pname, files in actual.items():
        pid = person_by_name.get(pname)
        if pid is None:
            details.append(f"目录「{pname}」不在名单里，已跳过")
            continue
        for fname in sorted(files):
            if fname in expected.get(pname, set()):
                continue
            ph = photos.get(fname)
            if ph is None:
                stem, dot, ext = fname.rpartition(".")
                ph = photos.get(stem.split("~")[0] + (("." + ext) if dot else ""))
            if ph is None:
                details.append(f"「{pname}/{fname}」对不上任何照片，已跳过")
                continue
            ostore.add(
                "photo_person", "present", photo_hash=ph["hash"], person_id=pid,
                value="present", source="adopt", operator=operator,
                reason="从 out/ 目录手动拖入", batch=batch,
            )
            added += 1
            details.append(f"+ {pname}/{fname}")
    for pname, files in expected.items():
        pid = person_by_name.get(pname)
        for fname in sorted(files):
            if fname in actual.get(pname, set()):
                continue
            ph = photos.get(fname)
            if ph is None:
                continue
            ostore.add(
                "photo_person", "absent", photo_hash=ph["hash"], person_id=pid,
                value="absent", source="adopt", operator=operator,
                reason="out/ 里被手动移出", batch=batch,
            )
            removed += 1
            details.append(f"- {pname}/{fname}")
    if added or removed:
        sstore.note_edit(batch, by=operator, note=f"adopt: +{added} -{removed}")
    return {"added": added, "removed": removed, "details": details}


def diff_auto_vs_manual(view: BatchView) -> dict:
    """对比「纯自动」vs「含人工」两套结论。"""
    rows = []
    for p in view.persons:
        auto = p.auto_count
        final = p.present_count
        manual_added = sum(1 for x in p.photos if x.manual and x.auto_status != "present")
        manual_removed = sum(
            1 for x in view.pairs.values()
            if x.person_id == p.person_id and x.manual and x.status == "absent"
            and x.auto_status == "present"
        )
        rows.append(
            {
                "name": p.name,
                "auto": auto,
                "final": final,
                "delta": final - auto,
                "manual_added": manual_added,
                "manual_removed": manual_removed,
            }
        )
    return {
        "batch": view.batch,
        "persons": rows,
        "auto_total": sum(r["auto"] for r in rows),
        "final_total": sum(r["final"] for r in rows),
    }
