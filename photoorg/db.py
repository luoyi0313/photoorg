"""存储层：persons.db（全局人员/班级/底库）、faces.db（不可变推理产物）、overrides.db（人工修正）。

三库物理分离（§9、§8.4）：
- faces.db  可以整表删掉重算，人工修正一条不丢；
- overrides.db 只追加（append-only），撤销 = 给最新一条打 superseded 墓碑；
- 最终结论永远是「自动推理 ⨝ 人工覆盖」在查询/重建时合并出来的。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np

from .util import now_iso

# ---------------------------------------------------------------- schema

PERSONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS persons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS classes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
    class_id INTEGER NOT NULL,
    person_id INTEGER NOT NULL,
    student_no TEXT DEFAULT '',
    role TEXT DEFAULT '',
    PRIMARY KEY (class_id, person_id)
);
CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL,
    person_id INTEGER NOT NULL,
    path TEXT DEFAULT '',
    photo_hash TEXT DEFAULT '',
    face_idx INTEGER,
    embedding BLOB NOT NULL,
    quality REAL DEFAULT 0,
    yaw REAL DEFAULT 0,
    iod REAL DEFAULT 0,
    source TEXT DEFAULT 'manual',
    active INTEGER DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tpl_person ON templates(class_id, person_id, active);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

FACES_SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
    hash TEXT PRIMARY KEY,
    batch TEXT NOT NULL,
    rel TEXT NOT NULL,
    abs TEXT NOT NULL,
    name TEXT NOT NULL,
    mtime REAL DEFAULT 0,
    size INTEGER DEFAULT 0,
    width INTEGER DEFAULT 0,
    height INTEGER DEFAULT 0,
    n_faces INTEGER DEFAULT 0,
    status TEXT DEFAULT 'ok',
    error TEXT DEFAULT '',
    model_ver TEXT DEFAULT '',
    quality REAL DEFAULT 0,
    processed_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_photos_batch ON photos(batch);
CREATE TABLE IF NOT EXISTS faces (
    photo_hash TEXT NOT NULL,
    face_idx INTEGER NOT NULL,
    bbox TEXT NOT NULL,
    det_score REAL DEFAULT 0,
    quality REAL DEFAULT 0,
    yaw REAL DEFAULT 0,
    pitch REAL DEFAULT 0,
    iod REAL DEFAULT 0,
    face_w REAL DEFAULT 0,
    blur REAL DEFAULT 0,
    brightness REAL DEFAULT 0,
    usable INTEGER DEFAULT 0,
    reasons TEXT DEFAULT '',
    embedding BLOB NOT NULL,
    model_ver TEXT DEFAULT '',
    PRIMARY KEY (photo_hash, face_idx)
);
CREATE TABLE IF NOT EXISTS decisions (
    photo_hash TEXT NOT NULL,
    face_idx INTEGER NOT NULL,
    status TEXT NOT NULL,
    person_id INTEGER,
    score REAL DEFAULT 0,
    s2 REAL DEFAULT 0,
    margin REAL DEFAULT 0,
    candidates TEXT DEFAULT '[]',
    PRIMARY KEY (photo_hash, face_idx)
);
CREATE TABLE IF NOT EXISTS pairs (
    photo_hash TEXT NOT NULL,
    person_id INTEGER NOT NULL,
    score REAL DEFAULT 0,
    status TEXT NOT NULL,
    face_idx INTEGER DEFAULT -1,
    PRIMARY KEY (photo_hash, person_id)
);
CREATE INDEX IF NOT EXISTS idx_pairs_person ON pairs(person_id);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

OVERRIDES_SCHEMA = """
CREATE TABLE IF NOT EXISTS overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    batch TEXT DEFAULT '',
    photo_hash TEXT DEFAULT '',
    person_id INTEGER,
    face_uid TEXT DEFAULT '',
    action TEXT NOT NULL,
    value TEXT DEFAULT '',
    source TEXT DEFAULT 'ui',
    operator TEXT DEFAULT '',
    reason TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    superseded INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ov_key ON overrides(scope, photo_hash, person_id, superseded);
CREATE INDEX IF NOT EXISTS idx_ov_batch ON overrides(batch, superseded);
"""


@contextmanager
def _conn(path: Path, schema: str | None = None) -> Iterator[sqlite3.Connection]:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    try:
        if schema:
            con.executescript(schema)
            con.commit()
        yield con
        con.commit()
    finally:
        con.close()


def _rows(cur: sqlite3.Cursor) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------- persons


class PersonStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        with _conn(self.path, PERSONS_SCHEMA) as con:
            yield con

    # -- persons
    def upsert_person(self, name: str, note: str = "") -> int:
        name = name.strip()
        if not name:
            raise ValueError("人名不能为空")
        with self.conn() as con:
            row = con.execute("SELECT id FROM persons WHERE name=?", (name,)).fetchone()
            if row:
                return int(row["id"])
            cur = con.execute(
                "INSERT INTO persons(name, note, created_at) VALUES(?,?,?)",
                (name, note, now_iso()),
            )
            return int(cur.lastrowid)

    def person(self, person_id: int) -> dict | None:
        with self.conn() as con:
            r = con.execute("SELECT * FROM persons WHERE id=?", (person_id,)).fetchone()
            return dict(r) if r else None

    def person_by_name(self, name: str) -> dict | None:
        with self.conn() as con:
            r = con.execute("SELECT * FROM persons WHERE name=?", (name.strip(),)).fetchone()
            return dict(r) if r else None

    def persons(self, ids: Iterable[int] | None = None) -> list[dict]:
        with self.conn() as con:
            if ids is None:
                return _rows(con.execute("SELECT * FROM persons ORDER BY id"))
            ids = list(ids)
            if not ids:
                return []
            q = ",".join("?" * len(ids))
            return _rows(con.execute(f"SELECT * FROM persons WHERE id IN ({q})", ids))

    def rename_person(self, person_id: int, new_name: str) -> None:
        with self.conn() as con:
            con.execute("UPDATE persons SET name=? WHERE id=?", (new_name.strip(), person_id))

    def delete_person(self, person_id: int) -> None:
        with self.conn() as con:
            con.execute("DELETE FROM persons WHERE id=?", (person_id,))
            con.execute("DELETE FROM members WHERE person_id=?", (person_id,))
            con.execute("DELETE FROM templates WHERE person_id=?", (person_id,))

    # -- classes
    def delete_class(self, name: str) -> int | None:
        """删掉班级及其成员/模板关系，返回 class_id（不存在则 None）。

        全局 persons 不删：同一个人可能在别的班里，且历史统计仍需要人名。
        """
        with self.conn() as con:
            row = con.execute("SELECT id FROM classes WHERE name=?", (name.strip(),)).fetchone()
            if not row:
                return None
            cid = int(row["id"])
            con.execute("DELETE FROM templates WHERE class_id=?", (cid,))
            con.execute("DELETE FROM members WHERE class_id=?", (cid,))
            con.execute("DELETE FROM classes WHERE id=?", (cid,))
            return cid

    def upsert_class(self, name: str, note: str = "") -> int:
        name = name.strip()
        if not name:
            raise ValueError("班级名不能为空")
        with self.conn() as con:
            row = con.execute("SELECT id FROM classes WHERE name=?", (name,)).fetchone()
            if row:
                return int(row["id"])
            cur = con.execute(
                "INSERT INTO classes(name, note, created_at) VALUES(?,?,?)",
                (name, note, now_iso()),
            )
            return int(cur.lastrowid)

    def klass(self, name: str) -> dict | None:
        with self.conn() as con:
            r = con.execute("SELECT * FROM classes WHERE name=?", (name.strip(),)).fetchone()
            return dict(r) if r else None

    def classes(self) -> list[dict]:
        with self.conn() as con:
            return _rows(con.execute("SELECT * FROM classes ORDER BY id"))

    # -- members
    def add_member(self, class_id: int, person_id: int, student_no: str = "", role: str = "") -> None:
        with self.conn() as con:
            con.execute(
                "INSERT OR REPLACE INTO members(class_id, person_id, student_no, role) VALUES(?,?,?,?)",
                (class_id, person_id, student_no, role),
            )

    def remove_member(self, class_id: int, person_id: int) -> None:
        with self.conn() as con:
            con.execute("DELETE FROM members WHERE class_id=? AND person_id=?", (class_id, person_id))

    def roster(self, class_id: int) -> list[dict]:
        with self.conn() as con:
            return _rows(
                con.execute(
                    """SELECT p.id, p.name, m.student_no, m.role
                       FROM members m JOIN persons p ON p.id=m.person_id
                       WHERE m.class_id=? ORDER BY p.id""",
                    (class_id,),
                )
            )

    # -- templates
    def add_template(
        self,
        class_id: int,
        person_id: int,
        embedding: np.ndarray,
        *,
        path: str = "",
        photo_hash: str = "",
        face_idx: int | None = None,
        quality: float = 0.0,
        yaw: float = 0.0,
        iod: float = 0.0,
        source: str = "manual",
        created_at: str | None = None,
    ) -> int:
        emb = np.asarray(embedding, dtype=np.float32).reshape(-1)
        n = float(np.linalg.norm(emb))
        if n > 0:
            emb = emb / n
        with self.conn() as con:
            cur = con.execute(
                """INSERT INTO templates(class_id, person_id, path, photo_hash, face_idx,
                                         embedding, quality, yaw, iod, source, active, created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,1,?)""",
                (
                    class_id, person_id, path, photo_hash, face_idx,
                    emb.tobytes(), float(quality), float(yaw), float(iod),
                    source, created_at or now_iso(),
                ),
            )
            return int(cur.lastrowid)

    def templates(self, class_id: int, person_id: int | None = None, active_only: bool = True) -> list[dict]:
        q = "SELECT * FROM templates WHERE class_id=?"
        args: list[Any] = [class_id]
        if person_id is not None:
            q += " AND person_id=?"
            args.append(person_id)
        if active_only:
            q += " AND active=1"
        q += " ORDER BY person_id, id"
        with self.conn() as con:
            return _rows(con.execute(q, args))

    def template_matrix(self, class_id: int) -> tuple[np.ndarray, np.ndarray, list[dict]]:
        """返回 (embeddings (M,512), person_ids (M,), rows)。"""
        rows = self.templates(class_id)
        if not rows:
            return np.zeros((0, 512), dtype=np.float32), np.zeros((0,), dtype=np.int64), []
        embs = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
        pids = np.array([r["person_id"] for r in rows], dtype=np.int64)
        return embs, pids, rows

    def set_template_active(self, template_id: int, active: bool) -> None:
        with self.conn() as con:
            con.execute("UPDATE templates SET active=? WHERE id=?", (1 if active else 0, template_id))

    def membership_count(self, person_id: int) -> int:
        with self.conn() as con:
            return int(
                con.execute("SELECT COUNT(*) FROM members WHERE person_id=?", (person_id,)).fetchone()[0]
            )

    def delete_templates_for(self, class_id: int, person_id: int) -> int:
        with self.conn() as con:
            cur = con.execute(
                "DELETE FROM templates WHERE class_id=? AND person_id=?", (class_id, person_id)
            )
            return int(cur.rowcount)

    def delete_template(self, template_id: int) -> None:
        with self.conn() as con:
            con.execute("DELETE FROM templates WHERE id=?", (template_id,))

    def template(self, template_id: int) -> dict | None:
        with self.conn() as con:
            r = con.execute("SELECT * FROM templates WHERE id=?", (template_id,)).fetchone()
            return dict(r) if r else None

    # -- meta
    def meta_get(self, key: str, default: str = "") -> str:
        with self.conn() as con:
            r = con.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
            return r["v"] if r else default

    def meta_set(self, key: str, value: str) -> None:
        with self.conn() as con:
            con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (key, str(value)))


# ---------------------------------------------------------------- per-class inference


class ClassDB:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        with _conn(self.path, FACES_SCHEMA) as con:
            yield con

    # -- photos
    def upsert_photo(
        self,
        hash: str,
        batch: str,
        rel: str,
        abs_path: str,
        name: str,
        *,
        mtime: float = 0,
        size: int = 0,
        width: int = 0,
        height: int = 0,
        n_faces: int = 0,
        status: str = "ok",
        error: str = "",
        model_ver: str = "",
        quality: float = 0.0,
        processed_at: str | None = None,
    ) -> None:
        with self.conn() as con:
            con.execute(
                """INSERT INTO photos(hash,batch,rel,abs,name,mtime,size,width,height,n_faces,
                                      status,error,model_ver,quality,processed_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(hash) DO UPDATE SET
                     batch=excluded.batch, rel=excluded.rel, abs=excluded.abs, name=excluded.name,
                     mtime=excluded.mtime, size=excluded.size, width=excluded.width,
                     height=excluded.height, n_faces=excluded.n_faces, status=excluded.status,
                     error=excluded.error, model_ver=excluded.model_ver, quality=excluded.quality,
                     processed_at=excluded.processed_at""",
                (
                    hash, batch, rel, abs_path, name, mtime, size, width, height, n_faces,
                    status, error, model_ver, quality, processed_at or now_iso(),
                ),
            )

    def photo(self, hash: str) -> dict | None:
        with self.conn() as con:
            r = con.execute("SELECT * FROM photos WHERE hash=?", (hash,)).fetchone()
            return dict(r) if r else None

    def photo_by_rel(self, batch: str, rel: str) -> dict | None:
        with self.conn() as con:
            r = con.execute(
                "SELECT * FROM photos WHERE batch=? AND rel=?", (batch, rel)
            ).fetchone()
            return dict(r) if r else None

    def photos(self, batch: str | None = None, status: str | None = None) -> list[dict]:
        q = "SELECT * FROM photos WHERE 1=1"
        args: list[Any] = []
        if batch:
            q += " AND batch=?"
            args.append(batch)
        if status:
            q += " AND status=?"
            args.append(status)
        q += " ORDER BY name"
        with self.conn() as con:
            return _rows(con.execute(q, args))

    def batches(self) -> list[dict]:
        with self.conn() as con:
            return _rows(
                con.execute(
                    """SELECT batch, COUNT(*) n_photos,
                              SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) n_error,
                              MIN(processed_at) first_run, MAX(processed_at) last_run
                       FROM photos GROUP BY batch ORDER BY batch"""
                )
            )

    def rename_photo(self, hash: str, rel: str, abs_path: str) -> None:
        """照片改名/移动后仍能靠 hash 认出来（§8.4 表格）。"""
        with self.conn() as con:
            con.execute("UPDATE photos SET rel=?, abs=? WHERE hash=?", (rel, abs_path, hash))

    # -- faces
    def replace_faces(self, photo_hash: str, faces: list, model_ver: str) -> None:
        with self.conn() as con:
            con.execute("DELETE FROM faces WHERE photo_hash=?", (photo_hash,))
            for f in faces:
                con.execute(
                    """INSERT OR REPLACE INTO faces(photo_hash,face_idx,bbox,det_score,quality,
                        yaw,pitch,iod,face_w,blur,brightness,usable,reasons,embedding,model_ver)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        photo_hash, f.face_idx,
                        ",".join(f"{v:.1f}" for v in f.bbox),
                        float(f.det_score), float(f.quality), float(f.yaw), float(f.pitch),
                        float(f.iod), float(f.face_w), float(f.blur), float(f.brightness),
                        1 if f.usable else 0, ",".join(f.reasons),
                        np.asarray(f.embedding, dtype=np.float32).tobytes(), model_ver,
                    ),
                )

    def faces(self, photo_hash: str | None = None) -> list[dict]:
        q = "SELECT * FROM faces"
        args: list[Any] = []
        if photo_hash:
            q += " WHERE photo_hash=?"
            args.append(photo_hash)
        q += " ORDER BY photo_hash, face_idx"
        with self.conn() as con:
            out = _rows(con.execute(q, args))
        for r in out:
            r["bbox"] = [float(x) for x in r["bbox"].split(",")]
        return out

    def face_embeddings(self, batch: str | None = None, usable_only: bool = False):
        """返回 (hashes, idxs, matrix(M,512))。"""
        q = """SELECT f.photo_hash, f.face_idx, f.embedding FROM faces f
               JOIN photos p ON p.hash=f.photo_hash WHERE 1=1"""
        args: list[Any] = []
        if batch:
            q += " AND p.batch=?"
            args.append(batch)
        if usable_only:
            q += " AND f.usable=1"
        q += " ORDER BY f.photo_hash, f.face_idx"
        with self.conn() as con:
            rows = con.execute(q, args).fetchall()
        if not rows:
            return [], [], np.zeros((0, 512), dtype=np.float32)
        hashes = [r["photo_hash"] for r in rows]
        idxs = [r["face_idx"] for r in rows]
        mat = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
        return hashes, idxs, mat

    def stale_photos(self, model_ver: str) -> list[str]:
        with self.conn() as con:
            return [
                r["hash"]
                for r in con.execute(
                    "SELECT hash FROM photos WHERE model_ver<>? AND status='ok'", (model_ver,)
                )
            ]

    # -- decisions / pairs (派生结论，可随时重算)
    def clear_decisions(self, batch: str | None = None) -> None:
        with self.conn() as con:
            if batch:
                con.execute(
                    "DELETE FROM decisions WHERE photo_hash IN (SELECT hash FROM photos WHERE batch=?)",
                    (batch,),
                )
                con.execute(
                    "DELETE FROM pairs WHERE photo_hash IN (SELECT hash FROM photos WHERE batch=?)",
                    (batch,),
                )
            else:
                con.execute("DELETE FROM decisions")
                con.execute("DELETE FROM pairs")

    def set_decisions(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self.conn() as con:
            con.executemany(
                """INSERT OR REPLACE INTO decisions(photo_hash,face_idx,status,person_id,
                       score,s2,margin,candidates) VALUES(?,?,?,?,?,?,?,?)""",
                [
                    (
                        r["photo_hash"], r["face_idx"], r["status"], r.get("person_id"),
                        r.get("score", 0.0), r.get("s2", 0.0), r.get("margin", 0.0),
                        r.get("candidates", "[]"),
                    )
                    for r in rows
                ],
            )

    def decisions(self, batch: str | None = None) -> list[dict]:
        q = """SELECT d.* FROM decisions d JOIN photos p ON p.hash=d.photo_hash WHERE 1=1"""
        args: list[Any] = []
        if batch:
            q += " AND p.batch=?"
            args.append(batch)
        with self.conn() as con:
            return _rows(con.execute(q, args))

    def set_pairs(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self.conn() as con:
            con.executemany(
                "INSERT OR REPLACE INTO pairs(photo_hash,person_id,score,status,face_idx) VALUES(?,?,?,?,?)",
                [(r["photo_hash"], r["person_id"], r.get("score", 0.0), r["status"],
                  int(r.get("face_idx", -1))) for r in rows],
            )

    def pairs(self, batch: str | None = None, person_id: int | None = None) -> list[dict]:
        q = """SELECT pr.* FROM pairs pr JOIN photos p ON p.hash=pr.photo_hash WHERE 1=1"""
        args: list[Any] = []
        if batch:
            q += " AND p.batch=?"
            args.append(batch)
        if person_id is not None:
            q += " AND pr.person_id=?"
            args.append(person_id)
        with self.conn() as con:
            return _rows(con.execute(q, args))

    # -- 删除（照片 / 批次）
    def delete_photo(self, hash: str) -> None:
        """删掉一张照片及其人脸、决策、照片×人结论。"""
        with self.conn() as con:
            con.execute("DELETE FROM faces WHERE photo_hash=?", (hash,))
            con.execute("DELETE FROM decisions WHERE photo_hash=?", (hash,))
            con.execute("DELETE FROM pairs WHERE photo_hash=?", (hash,))
            con.execute("DELETE FROM photos WHERE hash=?", (hash,))

    def delete_batch(self, batch: str) -> int:
        """删掉一个批次的全部推理产物，返回删除的照片数。"""
        with self.conn() as con:
            hashes = [r["hash"] for r in con.execute("SELECT hash FROM photos WHERE batch=?", (batch,))]
            if not hashes:
                return 0
            marks = ",".join("?" * len(hashes))
            con.execute(f"DELETE FROM faces WHERE photo_hash IN ({marks})", hashes)
            con.execute(f"DELETE FROM decisions WHERE photo_hash IN ({marks})", hashes)
            con.execute(f"DELETE FROM pairs WHERE photo_hash IN ({marks})", hashes)
            con.execute(f"DELETE FROM photos WHERE hash IN ({marks})", hashes)
            return len(hashes)


# ---------------------------------------------------------------- overrides


class OverrideStore:
    """append-only 的人工决策表。

    生效规则：同一 key 下 **id 最大且 superseded=0** 的那条生效。
    撤销 = 把当前生效的那条标记 superseded=1（墓碑），自动回落到上一条。
    """

    KEY_FIELDS = ("scope", "photo_hash", "person_id", "face_uid")

    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        with _conn(self.path, OVERRIDES_SCHEMA) as con:
            yield con

    def add(
        self,
        scope: str,
        action: str,
        *,
        photo_hash: str = "",
        person_id: int | None = None,
        face_uid: str = "",
        value: str = "",
        source: str = "ui",
        operator: str = "",
        reason: str = "",
        batch: str = "",
    ) -> int:
        with self.conn() as con:
            cur = con.execute(
                """INSERT INTO overrides(scope,batch,photo_hash,person_id,face_uid,action,value,
                                         source,operator,reason,created_at,superseded)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,0)""",
                (
                    scope, batch, photo_hash, person_id, face_uid, action, value,
                    source, operator, reason, now_iso(),
                ),
            )
            return int(cur.lastrowid)

    def all(self, batch: str | None = None, scope: str | None = None, include_superseded: bool = False) -> list[dict]:
        q = "SELECT * FROM overrides WHERE 1=1"
        args: list[Any] = []
        if batch:
            q += " AND batch=?"
            args.append(batch)
        if scope:
            q += " AND scope=?"
            args.append(scope)
        if not include_superseded:
            q += " AND superseded=0"
        q += " ORDER BY id"
        with self.conn() as con:
            return _rows(con.execute(q, args))

    def latest_per_key(self, batch: str | None = None) -> dict[tuple, dict]:
        """返回 {(scope, photo_hash, person_id, face_uid): 生效记录}。"""
        out: dict[tuple, dict] = {}
        for r in self.all(batch=batch):
            key = tuple(r[k] for k in self.KEY_FIELDS)
            prev = out.get(key)
            if prev is None or r["id"] > prev["id"]:
                out[key] = r
        return out

    def photo_person_map(self, batch: str | None = None) -> dict[tuple[str, int], dict]:
        """(photo_hash, person_id) → 生效的 photo_person 记录。"""
        out: dict[tuple[str, int], dict] = {}
        for r in self.all(batch=batch, scope="photo_person"):
            if not r["photo_hash"] or r["person_id"] is None:
                continue
            key = (r["photo_hash"], int(r["person_id"]))
            if key not in out or r["id"] > out[key]["id"]:
                out[key] = r
        return out

    def face_map(self, batch: str | None = None) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for r in self.all(batch=batch, scope="face_assign"):
            if not r["face_uid"]:
                continue
            if r["face_uid"] not in out or r["id"] > out[r["face_uid"]]["id"]:
                out[r["face_uid"]] = r
        return out

    def latest_for_key(
        self, scope: str, photo_hash: str = "", person_id: int | None = None, face_uid: str = ""
    ) -> dict | None:
        with self.conn() as con:
            r = con.execute(
                """SELECT * FROM overrides WHERE scope=? AND photo_hash=? AND face_uid=?
                   AND IFNULL(person_id,-1)=IFNULL(?,-1) AND superseded=0
                   ORDER BY id DESC LIMIT 1""",
                (scope, photo_hash, face_uid, person_id),
            ).fetchone()
            return dict(r) if r else None

    def supersede(self, override_id: int) -> None:
        with self.conn() as con:
            con.execute("UPDATE overrides SET superseded=1 WHERE id=?", (override_id,))

    def undo_last(self, batch: str | None = None, scope: str | None = None) -> dict | None:
        """撤销最近一条仍生效的修正，返回被撤销的记录。"""
        q = "SELECT * FROM overrides WHERE superseded=0"
        args: list[Any] = []
        if batch:
            q += " AND batch=?"
            args.append(batch)
        if scope:
            q += " AND scope=?"
            args.append(scope)
        q += " ORDER BY id DESC LIMIT 1"
        with self.conn() as con:
            r = con.execute(q, args).fetchone()
            if not r:
                return None
            con.execute("UPDATE overrides SET superseded=1 WHERE id=?", (r["id"],))
            return dict(r)

    def history(self, batch: str | None = None, limit: int = 200) -> list[dict]:
        q = "SELECT * FROM overrides WHERE 1=1"
        args: list[Any] = []
        if batch:
            q += " AND batch=?"
            args.append(batch)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self.conn() as con:
            return _rows(con.execute(q, args))

    def count_active(self, batch: str | None = None) -> int:
        return len(self.all(batch=batch))
