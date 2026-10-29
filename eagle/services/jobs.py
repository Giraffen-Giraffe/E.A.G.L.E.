"""処理キュー。取込んだ書類を1件ずつ(VLM は同時に1件)バックグラウンドで処理する。

キューは DB(jobs テーブル)に持つので、アプリを再起動しても未処理分から再開する。
"""
from __future__ import annotations

import logging
import threading
import time
import traceback

from ..db import Document, Job, session_scope, utcnow, log_event
from ..pipeline.processor import process_document
from ..vlm import VlmUnavailable

LOG = logging.getLogger("eagle.jobs")


class JobWorker:
    def __init__(self) -> None:
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.current: dict | None = None
        self.on_finished = []   # callbacks(doc_id, status)

    # ── 投入 ─────────────────────────────────────────────────────────
    def enqueue(self, document_id: int, options: dict | None = None, priority: int = 0) -> int:
        with session_scope() as s:
            # 同じ書類の未処理ジョブがあれば置き換える
            for j in s.query(Job).filter(Job.document_id == document_id, Job.status == "queued"):
                j.status = "cancelled"
            job = Job(document_id=document_id, options=options or {}, priority=priority)
            s.add(job)
            doc = s.get(Document, document_id)
            if doc is not None:
                doc.status, doc.status_detail = "queued", ""
            s.flush()
            jid = job.id
        self._wake.set()
        return jid

    def cancel(self, job_id: int) -> bool:
        with session_scope() as s:
            j = s.get(Job, job_id)
            if j is None or j.status != "queued":
                return False
            j.status, j.finished_at = "cancelled", utcnow()
            doc = s.get(Document, j.document_id)
            if doc is not None and doc.status == "queued":
                doc.status, doc.status_detail = "failed", "処理を取り消しました"
            return True

    # ── 実行 ─────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        with session_scope() as s:   # 前回異常終了時に実行中だったものは待ちに戻す
            for j in s.query(Job).filter(Job.status == "running"):
                j.status = "queued"
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="eagle-jobs", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _next(self) -> tuple[int, int, dict] | None:
        with session_scope() as s:
            j = (s.query(Job).filter(Job.status == "queued")
                 .order_by(Job.priority.desc(), Job.id.asc()).first())
            if j is None:
                return None
            j.status, j.started_at, j.progress, j.message = "running", utcnow(), 0.0, "開始"
            return j.id, j.document_id, dict(j.options or {})

    def _progress(self, job_id: int, frac: float, msg: str) -> None:
        if self.current is not None:
            self.current.update(progress=round(frac, 3), message=msg)
        try:
            with session_scope() as s:
                j = s.get(Job, job_id)
                if j is not None:
                    j.progress, j.message = round(frac, 3), msg
        except Exception:  # noqa: BLE001
            pass

    def _loop(self) -> None:
        while not self._stop.is_set():
            nxt = self._next()
            if nxt is None:
                self._wake.wait(timeout=5.0)
                self._wake.clear()
                continue
            job_id, doc_id, options = nxt
            self.current = {"job_id": job_id, "document_id": doc_id, "progress": 0.0, "message": "開始",
                            "started": time.time()}
            status, message = "done", "完了"
            try:
                res = process_document(doc_id, options, lambda f, m: self._progress(job_id, f, m))
                doc_status = res.get("status", "")
            except VlmUnavailable as exc:
                status, message, doc_status = "failed", f"VLM に接続できません: {exc}", "failed"
                self._fail_doc(doc_id, message)
            except Exception as exc:  # noqa: BLE001
                LOG.exception("書類 %s の処理に失敗しました", doc_id)
                status, message, doc_status = "failed", f"{type(exc).__name__}: {exc}", "failed"
                self._fail_doc(doc_id, message, traceback.format_exc(limit=6))
            with session_scope() as s:
                j = s.get(Job, job_id)
                if j is not None:
                    j.status, j.message, j.progress, j.finished_at = status, message[:2000], 1.0, utcnow()
            self.current = None
            for cb in list(self.on_finished):
                try:
                    cb(doc_id, doc_status)
                except Exception:  # noqa: BLE001
                    LOG.exception("処理完了の後続処理に失敗しました")

    @staticmethod
    def _fail_doc(doc_id: int, message: str, detail: str = "") -> None:
        with session_scope() as s:
            d = s.get(Document, doc_id)
            if d is not None:
                d.status, d.status_detail = "failed", (message + ("\n" + detail if detail else ""))[:4000]
        log_event(message, "process", "error", document_id=doc_id)

    def status(self) -> dict:
        with session_scope() as s:
            queued = s.query(Job).filter(Job.status == "queued").count()
            recent = (s.query(Job).order_by(Job.id.desc()).limit(20).all())
            items = [{"id": j.id, "document_id": j.document_id, "status": j.status, "progress": j.progress,
                      "message": j.message, "created_at": j.created_at.isoformat() if j.created_at else None,
                      "finished_at": j.finished_at.isoformat() if j.finished_at else None} for j in recent]
        cur = dict(self.current) if self.current else None
        if cur:
            cur["elapsed"] = round(time.time() - cur.pop("started"), 1)
        return {"running": cur, "queued": queued, "recent": items,
                "alive": bool(self._thread and self._thread.is_alive())}


worker = JobWorker()
