"""批次状态机与定稿门禁（§8.6）。

RUNNING → AUTO_DONE → REVIEWED → PUBLISHED
            │            │           │
            │            │           └─ 报告可对外发（页眉带确认人+时间）
            │            └─ 灰区清零 + 未出现名单逐人确认
            └─ 自动跑完，报告标 DRAFT

PUBLISHED 后任何修改 → 自动回退到 REVIEWED（不会出现「改了但状态没变」）。
"""
from __future__ import annotations

from pathlib import Path

from .util import now_iso, read_json, write_json

RUNNING = "RUNNING"
AUTO_DONE = "AUTO_DONE"
REVIEWED = "REVIEWED"
PUBLISHED = "PUBLISHED"

ORDER = {RUNNING: 0, AUTO_DONE: 1, REVIEWED: 2, PUBLISHED: 3}

LABEL = {
    RUNNING: "DRAFT",
    AUTO_DONE: "DRAFT",
    REVIEWED: "REVIEWED",
    PUBLISHED: "PUBLISHED",
}


class StateStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _all(self) -> dict:
        return read_json(self.path, {}) or {}

    def _save(self, data: dict) -> None:
        write_json(self.path, data)

    def get(self, batch: str) -> dict:
        st = self._all().get(batch) or {}
        return {
            "batch": batch,
            "state": st.get("state", RUNNING),
            "label": LABEL.get(st.get("state", RUNNING), "DRAFT"),
            "run_at": st.get("run_at", ""),
            "updated_at": st.get("updated_at", ""),
            "confirmations": st.get("confirmations", {}),
            "published": st.get("published", {}),
            "history": st.get("history", []),
            "note": st.get("note", ""),
        }

    def _mutate(self, batch: str, fn) -> dict:
        data = self._all()
        rec = data.setdefault(batch, {"state": RUNNING, "history": [], "confirmations": {}})
        rec.setdefault("confirmations", {})
        rec.setdefault("history", [])
        fn(rec)
        rec["updated_at"] = now_iso()
        data[batch] = rec
        self._save(data)
        return self.get(batch)

    def set_state(self, batch: str, state: str, by: str = "", note: str = "") -> dict:
        if state not in ORDER:
            raise ValueError(f"未知状态: {state}")

        def fn(rec: dict) -> None:
            rec["state"] = state
            rec.setdefault("history", []).append(
                {"state": state, "by": by, "at": now_iso(), "note": note}
            )
            if state == PUBLISHED:
                rec["published"] = {"by": by, "at": now_iso()}
        return self._mutate(batch, fn)

    def mark_auto_done(self, batch: str, note: str = "") -> dict:
        def fn(rec: dict) -> None:
            rec["state"] = AUTO_DONE
            rec["run_at"] = now_iso()
            rec["confirmations"] = {}
            rec.pop("published", None)
            rec.setdefault("history", []).append(
                {"state": AUTO_DONE, "by": "system", "at": now_iso(), "note": note}
            )
        return self._mutate(batch, fn)

    def confirm_absent(self, batch: str, person_id: int, by: str = "") -> dict:
        def fn(rec: dict) -> None:
            rec.setdefault("confirmations", {})[str(person_id)] = {"by": by, "at": now_iso()}
            if rec.get("state") == PUBLISHED:
                rec["state"] = REVIEWED
                rec.setdefault("history", []).append(
                    {"state": REVIEWED, "by": by, "at": now_iso(), "note": "未出现名单变更，退回 REVIEWED"}
                )
        return self._mutate(batch, fn)

    def unconfirm_absent(self, batch: str, person_id: int) -> dict:
        def fn(rec: dict) -> None:
            rec.setdefault("confirmations", {}).pop(str(person_id), None)
        return self._mutate(batch, fn)

    def clear_confirmations(self, batch: str) -> dict:
        def fn(rec: dict) -> None:
            rec["confirmations"] = {}
        return self._mutate(batch, fn)

    def note_edit(self, batch: str, by: str = "", note: str = "") -> dict:
        """任何人工修改之后调用：PUBLISHED → REVIEWED。"""

        def fn(rec: dict) -> None:
            if rec.get("state") == PUBLISHED:
                rec["state"] = REVIEWED
                rec.pop("published", None)
                rec.setdefault("history", []).append(
                    {"state": REVIEWED, "by": by, "at": now_iso(), "note": note or "人工修改后自动回退"}
                )
            if rec.get("state") in (None, RUNNING):
                rec["state"] = AUTO_DONE
        return self._mutate(batch, fn)

    def remove(self, batch: str) -> None:
        """批次被删除时，连它的状态记录一起清掉。"""
        data = self._all()
        if batch in data:
            data.pop(batch, None)
            self._save(data)

    def confirmations(self, batch: str) -> set[int]:
        st = self.get(batch)
        return {int(k) for k in (st.get("confirmations") or {}).keys()}
