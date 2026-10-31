"""命令行入口（§8.3-D）。

CLI 是「没有界面时也要能改结论」的那个兜底，所以 fix / overrides / rebuild / diff
是一条完整闭环，不依赖 Web。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import webbrowser
from pathlib import Path

from . import __version__
from .archive import adopt as adopt_impl
from .archive import diff_auto_vs_manual, rebuild as rebuild_impl
from .calibrate import apply as apply_thresholds
from .calibrate import suggest as suggest_thresholds
from .cluster import accept_cluster, cluster_faces
from .config import AppPaths, Settings, default_model_root, safe_name
from .corrections import (
    CSV_HEADER,
    decide_face,
    export_overrides_csv,
    import_overrides_csv,
    set_photo_person,
    undo as undo_impl,
)
from .db import ClassDB, OverrideStore, PersonStore
from .imaging import iter_images
from .pipeline import (
    UserError,
    batch_meta,
    load_settings,
    rematch,
    resolve_batch_dir,
    run_batch,
    save_settings,
)
from .report import build_markdown
from .results import compute_batch
from .state import PUBLISHED, REVIEWED, StateStore
from .templates import (
    delete_person_with_gallery,
    enroll_files,
    enroll_from_dirs,
    health as gallery_health,
)

USER = os.environ.get("PHOTOORG_OPERATOR") or os.environ.get("USER") or "me"


# ------------------------------------------------------------------ helpers


def _app(args) -> AppPaths:
    return AppPaths(Path(getattr(args, "root", None) or "."))


def _class(args) -> str:
    name = getattr(args, "class_name", None)
    if name:
        return name
    cfg = _app(args).global_config
    if cfg.exists():
        try:
            d = json.loads(cfg.read_text("utf-8"))
            if d.get("default_class"):
                return d["default_class"]
        except Exception:
            pass
    raise UserError("请用 --class 指定班级（例如 --class 三年二班）")


def _set_default_class(app: AppPaths, name: str) -> None:
    d = {}
    if app.global_config.exists():
        try:
            d = json.loads(app.global_config.read_text("utf-8"))
        except Exception:
            d = {}
    d["default_class"] = name
    app.ensure()
    app.global_config.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", "utf-8")


def _ok(msg: str) -> None:
    print(f"✅ {msg}")


def _warn(msg: str) -> None:
    print(f"⚠️  {msg}")


def _fail(msg: str, code: int = 2) -> int:
    print(f"❌ {msg}", file=sys.stderr)
    return code


# ------------------------------------------------------------------ commands


def cmd_init(args) -> int:
    app = _app(args)
    app.ensure()
    for d in (app.photos, app.data / "models"):
        d.mkdir(parents=True, exist_ok=True)
    class_name = args.class_name or "我的班"
    store = PersonStore(app.persons_db)
    store.upsert_class(class_name)
    _set_default_class(app, class_name)
    roster = Path(args.roster) if args.roster else (app.root / "roster")
    roster.mkdir(parents=True, exist_ok=True)
    print(f"工作区：{app.root}")
    print(f"  照片：{app.photos}/<批次目录>/   （目录名 = 日期-活动，如 20261029-秋游）")
    print(f"  名单：{roster}/<人名>/*.jpg   （3~10 张底库，然后用 photoorg enroll --from {roster}）")
    print(f"  数据：{app.data}/")
    print(f"  产物：{app.out}/")
    _ok(f"初始化完成，默认班级「{class_name}」")
    return 0


def cmd_person(args) -> int:
    app = _app(args)
    class_name = _class(args)
    store = PersonStore(app.persons_db)
    class_id = store.upsert_class(class_name)
    if args.action == "add":
        for name in args.names:
            pid = store.upsert_person(name)
            store.add_member(class_id, pid)
            _ok(f"加入名单：{name} (#{pid})")
        return 0
    if args.action == "list":
        roster = store.roster(class_id)
        if not roster:
            print("名单是空的。photoorg person add --class {0} 张三 李四".format(class_name))
            return 0
        for r in roster:
            n = len(store.templates(class_id, int(r["id"])))
            print(f"#{r['id']:<4} {r['name']:<10} 模板 {n} 张")
        print(f"共 {len(roster)} 人")
        return 0
    if args.action == "rm":
        roster = {r["name"]: int(r["id"]) for r in store.roster(class_id)}
        targets = []
        for name in args.names:
            if name in roster:
                targets.append((name, roster[name]))
            elif name.isdigit() and int(name) in {r["id"] for r in store.roster(class_id)}:
                targets.append((f"#{name}", int(name)))
            else:
                _warn(f"名单里没有「{name}」")
        if not targets:
            return 0
        if not args.yes:
            print("将删除：" + "、".join(n for n, _ in targets))
            print("这会同时删除他们的底库模板（历史批次的人工修正会保留）。确认请加 --yes")
            return 1
        for name, pid in targets:
            delete_person_with_gallery(app, class_name, pid)
            _ok(f"已删除 {name}（及其底库）")
        return 0
    return 0


def cmd_enroll(args) -> int:
    app = _app(args)
    class_name = _class(args)
    if args.person:
        files: list[Path] = []
        for f in args.files:
            p = Path(f)
            files.append(p)
        r = enroll_files(app, class_name, args.person, files)
        _ok(f"{r['person']}：入底库 {r['added']} 张")
        for x in r["rejected"]:
            _warn(x)
        return 0
    src = Path(args.from_dir)
    print(f"从 {src} 批量入库……（第一次会自动下载人脸模型）")
    r = enroll_from_dirs(app, class_name, src)
    total = sum(p["added"] for p in r["persons"])
    for p in r["persons"]:
        print(f"  {p['person']}: +{p['added']}" + (f"  ({len(p['rejected'])} 张被拒)" if p["rejected"] else ""))
        for x in p["rejected"][:3]:
            print(f"      - {x}")
    _ok(f"共 {len(r['persons'])} 人、{total} 张模板入库")
    if total:
        print("下一步：photoorg run --batch <批次>")
    return 0


def cmd_run(args) -> int:
    app = _app(args)
    class_name = _class(args)
    settings = load_settings(app, class_name)
    if args.det_size:
        settings.engine["det_size"] = int(args.det_size)
        settings.engine["det_size_group"] = max(int(args.det_size), 1024)
    cp = app.class_paths(class_name)
    store = PersonStore(app.persons_db)
    klass = store.klass(class_name)
    if klass and not store.templates(int(klass["id"])):
        _warn("底库还是空的：这次只会检脸，不会归档。先 photoorg enroll 或 photoorg cluster")

    def progress(done: int, total: int, name: str) -> None:
        if not sys.stdout.isatty():
            return
        bar = int(30 * done / max(1, total))
        print(f"\r  [{'#' * bar}{'.' * (30 - bar)}] {done}/{total} {name[:28]:<28}", end="", flush=True)

    t0 = time.perf_counter()
    stats = run_batch(app, class_name, args.batch, force=args.force, limit=args.limit,
                      settings=settings, progress=progress)
    if sys.stdout.isatty():
        print()
    print(f"批次 {stats.batch}：{stats.n_total} 张（新增 {stats.n_new}，复用 {stats.n_skipped}，"
          f"失败 {stats.n_error}），检出 {stats.n_faces} 张脸，用时 {stats.elapsed:.1f}s")
    if stats.errors:
        for e in stats.errors[:10]:
            _warn(e)

    if args.no_rebuild:
        _ok("已跳过归档（--no-rebuild）。要出 out/ 请跑 photoorg rebuild")
        return 0
    view = compute_batch(app, class_name, stats.batch, settings)
    r = rebuild_impl(app, class_name, stats.batch, view=view)
    print(f"归档：{r['n_link']} 个硬链接 / {r['n_copy']} 个复制（新增 {r['n_add']}、移除 {r['n_remove']}），"
          f"{r['elapsed']:.2f}s → {r['target']}")
    print(f"出现 {view.n_present_pairs} 条；灰区 {len(view.review_items)} 条；"
          f"未出现 {len(view.absent_persons)} 人；名单外的人脸 {len(view.unknown_faces)} 张")
    if view.stats.get("incomplete"):
        _warn(view.stats["incomplete_reason"] + " → 整批 INCOMPLETE，不出「未出现」结论")
    if view.review_items:
        print(f"下一步：photoorg review  （{len(view.review_items)} 条灰区，2~3 分钟过完）")
    elif view.absent_persons:
        print("下一步：确认「未出现」名单 → photoorg review 或直接看 out/{batch}/report.md".format(batch=stats.batch))
    elif klass and not store.templates(int(klass["id"])):
        pass
    return 0


def cmd_rebuild(args) -> int:
    app = _app(args)
    class_name = _class(args)
    batch = args.batch or resolve_batch_dir(app, None).name
    t0 = time.perf_counter()
    r = rebuild_impl(app, class_name, batch, dry_run=args.dry_run)
    if r["dry_run"]:
        print(f"重建预览（{batch}）：将新增 {r['n_add']}、移除 {r['n_remove']} 个文件")
        for d, n in r["added"][:20]:
            print(f"  + {d}/{n}")
        for d, n in r["removed"][:20]:
            print(f"  - {d}/{n}")
        return 0
    print(f"重建完成：{r['n_link']} 硬链接 / {r['n_copy']} 复制，新增 {r['n_add']}、移除 {r['n_remove']}，"
          f"{time.perf_counter() - t0:.2f}s")
    print(f"→ {r['target']}")
    return 0


def cmd_fix(args) -> int:
    app = _app(args)
    class_name = _class(args)
    batch = args.batch or resolve_batch_dir(app, None).name
    r = import_overrides_csv(app, class_name, batch, Path(args.csv), operator=USER)
    _ok(f"应用 {r['applied']} 条修正")
    for s in r["skipped"][:20]:
        _warn(s)
    if r["applied"]:
        rb = rebuild_impl(app, class_name, batch)
        print(f"已重建 out/{batch}：新增 {rb['n_add']}、移除 {rb['n_remove']}，{rb['elapsed']:.2f}s")
    return 0


def cmd_overrides(args) -> int:
    app = _app(args)
    class_name = _class(args)
    if args.action == "export":
        batch = args.batch or resolve_batch_dir(app, None).name
        text = export_overrides_csv(app, class_name, batch)
        out = Path(args.output or f"overrides-{batch}.csv")
        out.write_text(text, "utf-8")
        n = max(0, len(text.strip().splitlines()) - 1)
        _ok(f"导出 {n} 条人工修正 → {out}")
        return 0
    if args.action == "import":
        batch = args.batch or resolve_batch_dir(app, None).name
        r = import_overrides_csv(app, class_name, batch, Path(args.file), operator=USER)
        _ok(f"导入 {r['applied']} 条")
        for s in r["skipped"][:20]:
            _warn(s)
        rebuild_impl(app, class_name, batch)
        return 0
    if args.action == "list":
        batch = args.batch
        cp = app.class_paths(class_name)
        ostore = OverrideStore(cp.overrides_db)
        store = PersonStore(app.persons_db)
        cdb = ClassDB(cp.faces_db)
        people = {p["id"]: p["name"] for p in store.persons()}
        photos = {p["hash"]: p["name"] for p in cdb.photos(batch=batch)}
        rows = ostore.history(batch=batch, limit=args.limit)
        if not rows:
            print("还没有人工修正记录。")
            return 0
        for r in rows:
            mark = "（已撤销）" if r["superseded"] else ""
            print(
                f"#{r['id']:<5} {r['created_at'][:16].replace('T', ' ')}  {r['scope']:<12} "
                f"{r['action']:<8} {photos.get(r['photo_hash'], r['photo_hash'][:8]):<28} "
                f"{people.get(int(r['person_id']), '-') if r['person_id'] else '-':<10} "
                f"{r['source']:<6} {r['reason']}{mark}"
            )
        return 0
    if args.action == "undo":
        r = undo_impl(app, class_name, batch=args.batch)
        if r["undone"]:
            _ok(f"已撤销 #{r['override']['id']}（{r['override']['action']}）")
            if args.batch:
                rebuild_impl(app, class_name, args.batch)
        else:
            print("没有可撤销的修正。")
        return 0
    return 0


def cmd_review(args) -> int:
    app = _app(args)
    class_name = _class(args)
    port = int(args.port)
    url = f"http://127.0.0.1:{port}/?class={class_name}"
    print(f"本地界面：{url}")
    print("灰区复核页键盘：1/2/3 选人 · X 不认识 · + 新人 · Z 撤销 · Enter 下一条")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    import uvicorn  # 延迟导入

    from .web.app import create_app

    app_obj = create_app(app.root, default_class=class_name)
    uvicorn.run(app_obj, host=args.host, port=port, log_level="warning")
    return 0


def cmd_calibrate(args) -> int:
    app = _app(args)
    class_name = _class(args)
    s = suggest_thresholds(app, class_name)
    cur, sug = s["current"], s["suggested"]
    print(f"班级：{class_name}　依据：{s['basis']}（{s['n_labels']} 组）")
    if s["from_gallery_only"]:
        _warn("人工标注还不够（<30 组），这次主要靠底库内部样本对，结论偏乐观")
    print(f"当前 T_high={cur['t_high']:.2f}  T_low={cur['t_low']:.2f}  margin={cur['margin']:.2f}")
    print(f"建议 T_high={sug['t_high']:.2f}  T_low={sug['t_low']:.2f}")
    print(f"  灰区占比 {cur['grey']:.0%} → {sug['grey']:.0%}（{s['delta']['grey']:+.1f}pt）")
    print(f"  漏判     {cur['miss']:.1%} → {sug['miss']:.1%}（{s['delta']['miss']:+.1f}pt）")
    print(f"  误归     {cur['false_accept']:.1%} → {sug['false_accept']:.1%}（{s['delta']['false_accept']:+.1f}pt）")
    print(f"  {s['advice']}")
    if args.apply:
        if abs(sug["t_high"] - cur["t_high"]) < 1e-6 and abs(sug["t_low"] - cur["t_low"]) < 1e-6:
            print("建议值与当前一致，无需改动。")
            return 0
        r = apply_thresholds(app, class_name, sug["t_high"], sug["t_low"], sug["margin"], by=USER)
        _ok(f"已采纳：{r['old']} → {r['new']}（历史批次需要 photoorg rebuild 才反映新阈值）")
    else:
        print("采纳：photoorg calibrate --class %s --apply" % class_name)
    return 0


def cmd_adopt(args) -> int:
    app = _app(args)
    class_name = _class(args)
    batch = args.batch or resolve_batch_dir(app, None).name
    r = adopt_impl(app, class_name, batch, operator=USER)
    _ok(f"吸收手动调整：+{r['added']} / -{r['removed']}")
    for d in r["details"][:30]:
        print(f"  {d}")
    if r["added"] or r["removed"]:
        rb = rebuild_impl(app, class_name, batch)
        print(f"已按新结论重建 out/{batch}")
    return 0


def cmd_diff(args) -> int:
    app = _app(args)
    class_name = _class(args)
    batch = args.batch or resolve_batch_dir(app, None).name
    view = compute_batch(app, class_name, batch)
    d = diff_auto_vs_manual(view)
    print(f"批次 {batch}　纯自动 {d['auto_total']} 条 vs 含人工 {d['final_total']} 条")
    print(f"{'姓名':<12}{'自动':>6}{'最终':>6}{'变化':>6}{'人工加':>8}{'人工减':>8}")
    for r in d["persons"]:
        if r["delta"] or r["manual_added"] or r["manual_removed"]:
            print(f"{r['name']:<12}{r['auto']:>6}{r['final']:>6}{r['delta']:>+6}"
                  f"{r['manual_added']:>8}{r['manual_removed']:>8}")
    return 0


def cmd_report(args) -> int:
    app = _app(args)
    class_name = _class(args)
    batch = args.batch or resolve_batch_dir(app, None).name
    view = compute_batch(app, class_name, batch)
    text = build_markdown(view)
    out = Path(args.output) if args.output else None
    if out:
        out.write_text(text, "utf-8")
        _ok(f"报告已写入 {out}")
    else:
        print(text)
    return 0


def cmd_status(args) -> int:
    app = _app(args)
    class_name = _class(args)
    cp = app.class_paths(class_name)
    store = PersonStore(app.persons_db)
    cdb = ClassDB(cp.faces_db)
    sstore = StateStore(cp.batch_state)
    klass = store.klass(class_name)
    if not klass:
        return _fail(f"班级不存在：{class_name}（先 photoorg init 或 person add）")
    roster = store.roster(int(klass["id"]))
    print(f"班级：{class_name}　名单 {len(roster)} 人　底库 {len(store.templates(int(klass['id'])))} 张模板")
    batches = cdb.batches()
    if not batches:
        print("还没有跑过批次。photoorg run --batch <批次>")
        return 0
    for b in batches:
        st = sstore.get(b["batch"])
        v = compute_batch(app, class_name, b["batch"])
        print(
            f"  {b['batch']:<24} 状态 {st['label']:<9} 照片 {b['n_photos']:<5} "
            f"出现 {v.n_present_pairs:<4} 灰区 {len(v.review_items):<3} 未出现 {len(v.absent_persons):<3}"
            + ("  质量不达标" if v.stats.get("incomplete") else "")
        )
    return 0


def cmd_state(args) -> int:
    app = _app(args)
    class_name = _class(args)
    cp = app.class_paths(class_name)
    sstore = StateStore(cp.batch_state)
    batch = args.batch or resolve_batch_dir(app, None).name
    view = compute_batch(app, class_name, batch)
    st = sstore.get(batch)
    print(f"批次 {batch}：{st['label']}")
    print(f"  灰区：{len(view.review_items)} 条")
    unconf = [p.name for p in view.persons if p.absent and not p.absent_confirmed]
    print(f"  未出现未确认：{len(unconf)} 人" + (f"（{'、'.join(unconf[:8])}）" if unconf else ""))
    if args.action == "publish":
        if not args.by:
            return _fail("发布需要 --by 填确认人（报告页眉会印出来）")
        if view.review_items:
            return _fail(f"还有 {len(view.review_items)} 条灰区未确认，禁止发布正式名单")
        if unconf:
            return _fail(f"还有 {len(unconf)} 人「未出现」没逐人确认，禁止发布")
        sstore.set_state(batch, REVIEWED, by=args.by, note="CLI 确认")
        sstore.set_state(batch, PUBLISHED, by=args.by, note="CLI 发布")
        rebuild_impl(app, class_name, batch)
        _ok(f"已发布（确认人 {args.by}）")
        return 0
    if args.action == "confirm-absent":
        roster = {p.name: p.person_id for p in view.persons}
        for name in args.names:
            if name not in roster:
                _warn(f"名单里没有「{name}」")
                continue
            sstore.confirm_absent(batch, roster[name], by=args.by or USER)
            _ok(f"已确认「{name}」未出现")
        return 0
    if args.action == "confirm-all-absent":
        for p in view.persons:
            if p.absent:
                sstore.confirm_absent(batch, p.person_id, by=args.by or USER)
        _ok("已确认全部「未出现」（注意：逐个确认是为了防止手滑）")
        return 0
    return 0


def cmd_cluster(args) -> int:
    app = _app(args)
    class_name = _class(args)
    batch = args.batch or resolve_batch_dir(app, None).name
    clusters = cluster_faces(
        app, class_name, batch, threshold=args.threshold,
        min_size=args.min_size, only_unknown=args.only_unknown,
    )
    if not clusters:
        print("没有凑够人数的簇。可以把 --threshold 调低，或 --min-size 2。")
        return 0
    print(f"{len(clusters)} 个簇（阈值 {args.threshold}）：")
    for c in clusters:
        names = "、".join(sorted({m["photo_name"] for m in c.members[:4]}))
        print(f"  #{c.cid:<3} {c.size:>3} 张脸  {names}")
    if args.json:
        print(json.dumps(
            [{"cid": c.cid, "size": c.size, "members": c.members} for c in clusters],
            ensure_ascii=False,
        ))
    if args.name:
        cid, name = int(args.name[0]), args.name[1]
        c = next((x for x in clusters if x.cid == cid), None)
        if not c:
            return _fail(f"没有簇 #{cid}")
        r = accept_cluster(app, class_name, batch, name, c.members, operator=USER)
        _ok(f"簇 #{cid} → {name}：入底库 {r['templates_added']} 张，标注 {r['faces_assigned']} 张脸")
        rematch(app, class_name, batch)
        rebuild_impl(app, class_name, batch)
    return 0


def cmd_gallery(args) -> int:
    app = _app(args)
    class_name = _class(args)
    rows = gallery_health(app, class_name)
    if not rows:
        print("名单是空的。")
        return 0
    print(f"{'姓名':<12}{'模板':>4}{'质量':>7}  提示")
    for r in rows:
        print(f"{r['name']:<12}{r['n_templates']:>4}{r['quality']:>7.2f}  {'；'.join(r['warnings'])}")
    return 0


def cmd_batches(args) -> int:
    app = _app(args)
    class_name = _class(args)
    cp = app.class_paths(class_name)
    cdb = ClassDB(cp.faces_db)
    sstore = StateStore(cp.batch_state)
    rows = cdb.batches()
    if not rows:
        print("还没有批次。")
        return 0
    for b in rows:
        meta = batch_meta(b["batch"])
        st = sstore.get(b["batch"])
        print(f"{b['batch']:<24} {meta['date'] or '-':<12} {meta['activity']:<12} "
              f"{b['n_photos']:>5} 张  状态 {st['label']}")
    return 0


def cmd_bench(args) -> int:
    app = _app(args)
    class_name = _class(args)
    batch_dir = resolve_batch_dir(app, args.batch)
    files = iter_images(batch_dir)[: args.n]
    if not files:
        return _fail("没有照片")
    settings = load_settings(app, class_name)
    print(f"基准测试：{len(files)} 张，det_size={settings.engine['det_size']}"
          f"（合照 {settings.engine['det_size_group']}）")
    from .engine import FaceEngine
    t0 = time.perf_counter()
    engine = FaceEngine(settings)
    print(f"模型加载 {time.perf_counter() - t0:.1f}s　模型版本 {engine.model_ver}")
    from .imaging import imread
    t0, n_face = time.perf_counter(), 0
    for f in files:
        try:
            img = imread(f)
        except Exception:
            continue
        n_face += len(engine.analyze(img))
    dt = time.perf_counter() - t0
    print(f"{dt / len(files):.2f} 秒/张，{len(files) / dt * 3600:.0f} 张/小时，共检出 {n_face} 张脸")
    if dt / len(files) > 3:
        _warn("> 3 秒/张：检查是否装了 CUDA 版 onnxruntime 但无 GPU，或 det_size 开得过大")
    return 0


def cmd_doctor(args) -> int:
    import platform
    import numpy
    print(f"python      {sys.version.split()[0]} ({platform.machine()})")
    try:
        import cv2
        print(f"opencv      {cv2.__version__}")
    except Exception as e:
        print(f"opencv      ❌ {e}")
    try:
        import onnxruntime as ort
        print(f"onnxruntime {ort.__version__}  providers={ort.get_available_providers()}")
    except Exception as e:
        print(f"onnxruntime ❌ {e}")
    try:
        import insightface
        print(f"insightface {insightface.__version__}")
    except Exception as e:
        print(f"insightface ❌ {e}")
    print(f"numpy       {numpy.__version__}")
    model_dir = default_model_root() / "models" / "buffalo_l"
    if model_dir.exists():
        print(f"模型        {model_dir}")
    else:
        print(f"模型        未下载（首次运行自动下载，目标：{model_dir}）")
    app = _app(args)
    try:
        cls = _class(args)
        store = PersonStore(app.persons_db)
        k = store.klass(cls)
        if k:
            print(f"名单        {cls}: {len(store.roster(int(k['id'])))} 人 / "
                  f"{len(store.templates(int(k['id'])))} 张模板")
    except UserError:
        pass
    return 0


def cmd_inspect(args) -> int:
    """不开界面时看一眼某批次某人的结论（调试/自测用）。"""
    app = _app(args)
    class_name = _class(args)
    batch = args.batch or resolve_batch_dir(app, None).name
    view = compute_batch(app, class_name, batch)
    for p in view.persons:
        if args.person and args.person != p.name:
            continue
        print(f"{p.name}: {p.present_count} 张（auto {p.auto_count} / manual {p.manual_count}）"
              + (f"  灰区 {len(p.review_photos)}" if p.review_photos else ""))
        for pr in p.photos[:20]:
            print(f"    {pr.photo_name:<28} {pr.source:<16} {pr.reason}")
        if p.absent and p.evidence:
            print("    最像的 3 张：" + "；".join(f"{e['photo_name']}({e['score']:.2f})" for e in p.evidence))
    return 0


# ------------------------------------------------------------------ parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="photoorg",
        description="班级照片归档与出勤统计（本地跑，照片不出电脑）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""常用流程：
  photoorg init --class 三年二班
  photoorg person add --class 三年二班 张三 李四 …
  photoorg enroll --class 三年二班 --from ./roster
  photoorg run --class 三年二班 --batch 20261029-秋游
  photoorg review            # 起界面，过灰区 + 审查结果
  photoorg fix --class … --batch … --csv corrections.csv
  photoorg state publish --class … --batch … --by 罗老师
""",
    )
    p.add_argument("-v", "--version", action="version", version=f"photoorg {__version__}")
    p.add_argument("--root", help="工作区根目录（默认当前目录）")
    p.add_argument("--class", dest="class_name", help="班级名")
    sub = p.add_subparsers(dest="cmd", required=True)

    # argparse 的全局选项通常必须放在子命令前。为了遵循文档中更自然的
    # `photoorg run --class 班级 ...` 写法，也把 --class 注册到各子命令。
    def command(name: str, **kwargs):
        child = sub.add_parser(name, **kwargs)
        child.add_argument("--class", dest="class_name", help="班级名")
        return child

    sp = command("init", help="初始化工作区")
    sp.add_argument("--roster", help="名单照片目录")
    sp.set_defaults(func=cmd_init)

    sp = command("person", help="名单管理")
    sp.add_argument("action", choices=["add", "list", "rm"])
    sp.add_argument("names", nargs="*")
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(func=cmd_person)

    sp = command("enroll", help="底库入库（注册）")
    sp.add_argument("--from", dest="from_dir", help="人名/照片 目录树")
    sp.add_argument("--person", help="单个人名")
    sp.add_argument("--files", nargs="*", default=[], help="该人的照片")
    sp.set_defaults(func=cmd_enroll)

    sp = command("run", help="跑一个批次：检测→匹配→归档→报告")
    sp.add_argument("--batch")
    sp.add_argument("--limit", type=int, help="只跑前 N 张（先试跑）")
    sp.add_argument("--force", action="store_true", help="忽略缓存，强制重新推理")
    sp.add_argument("--det-size", type=int, help="检测分辨率（合照建议 1280）")
    sp.add_argument("--no-rebuild", action="store_true", help="不重建 out/")
    sp.set_defaults(func=cmd_run)

    sp = command("review", help="起本地网页界面（灰区复核 / 结果审查 / 底库）")
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--no-browser", action="store_true")
    sp.set_defaults(func=cmd_review)

    sp = command("fix", help="用 CSV 批量修正结论")
    sp.add_argument("--csv", required=True, help="列：photo,person,action,reason")
    sp.add_argument("--batch")
    sp.set_defaults(func=cmd_fix)

    sp = command("overrides", help="人工修正的导出/导入/查看/撤销")
    sp.add_argument("action", choices=["export", "import", "list", "undo"])
    sp.add_argument("file", nargs="?")
    sp.add_argument("-o", "--output")
    sp.add_argument("--batch")
    sp.add_argument("--limit", type=int, default=200)
    sp.set_defaults(func=cmd_overrides)

    sp = command("calibrate", help="用累积标注重算阈值（只给建议）")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_calibrate)

    sp = command("rebuild", help="重建 out/（全量）")
    sp.add_argument("--batch")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_rebuild)

    sp = command("adopt", help="吸收手动拖文件的调整")
    sp.add_argument("--batch")
    sp.set_defaults(func=cmd_adopt)

    sp = command("diff", help="对比「纯自动」vs「含人工」")
    sp.add_argument("--batch")
    sp.set_defaults(func=cmd_diff)

    sp = command("report", help="输出 report.md")
    sp.add_argument("--batch")
    sp.add_argument("-o", "--output")
    sp.set_defaults(func=cmd_report)

    sp = command("status", help="总览：名单/底库/各批次结论")
    sp.set_defaults(func=cmd_status)

    sp = command("batches", help="列出批次")
    sp.set_defaults(func=cmd_batches)

    sp = command("state", help="状态机与定稿门禁")
    sp.add_argument("action", choices=["show", "publish", "confirm-absent", "confirm-all-absent"])
    sp.add_argument("names", nargs="*")
    sp.add_argument("--batch")
    sp.add_argument("--by")
    sp.set_defaults(func=cmd_state)

    sp = command("cluster", help="冷启动聚类点名")
    sp.add_argument("--batch")
    sp.add_argument("--threshold", type=float, default=0.55)
    sp.add_argument("--min-size", type=int, default=2)
    sp.add_argument("--only-unknown", action="store_true")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--name", nargs=2, metavar=("CID", "NAME"), help="给某个簇命名")
    sp.set_defaults(func=cmd_cluster)

    sp = command("gallery", help="底库健康度")
    sp.set_defaults(func=cmd_gallery)

    sp = command("inspect", help="命令行查看某人结论（调试用）")
    sp.add_argument("--batch")
    sp.add_argument("--person")
    sp.set_defaults(func=cmd_inspect)

    sp = command("bench", help="吞吐基准测试")
    sp.add_argument("--batch")
    sp.add_argument("--n", type=int, default=30)
    sp.set_defaults(func=cmd_bench)

    sp = command("doctor", help="环境自检")
    sp.set_defaults(func=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except UserError as e:
        return _fail(str(e))
    except KeyboardInterrupt:
        print("\n已中断")
        return 130


if __name__ == "__main__":
    sys.exit(main())
