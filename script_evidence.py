"""Save loaded script response bodies by hash, without fetching URLs again.

Limits bound saved bytes and pending tasks. Playwright response.body() itself
materializes a response; unknown/incorrect Content-Length is not a memory cap.
"""
import asyncio
import hashlib
from pathlib import Path


class ScriptCapture:
    def __init__(self, db, state, root, *, max_script=2 * 1024**2, max_total=20 * 1024**2, max_pending=64):
        self.db, self.state, self.root = db, state, Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_script, self.max_total, self.max_pending = max_script, max_total, max_pending
        self.saved = 0
        self.tasks = set()
        self.slots = asyncio.Semaphore(4)

    def owner(self, request):
        row_id = self.state.req_rows.get(request)
        if row_id is None:
            return None
        row = self.db.execute("SELECT visit_id,frame_url FROM requests WHERE id=?", (row_id,)).fetchone()
        return (row[0], request.url, row[1]) if row else None

    def record(self, owner, status, *, digest=None, size=None, http=None, detail=None):
        self.db.execute("INSERT INTO script_sources(visit_id,url,frame_url,status,sha256,bytes,http_status,detail) "
                        "VALUES (?,?,?,?,?,?,?,?)", (*owner, status, digest, size, http, detail))

    def on_failed(self, request):
        if request.resource_type == "script":
            owner = self.owner(request)
            if owner:
                self.record(owner, "request_failed", detail=request.failure)

    def on_response(self, response):
        if response.request.resource_type != "script":
            return
        owner = self.owner(response.request)  # capture before requestfinished removes ownership
        if not owner:
            return
        if len(self.tasks) >= self.max_pending:
            self.record(owner, "pending_limit", http=response.status)
            return
        task = asyncio.create_task(self.capture(response, owner))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def capture(self, response, owner):
        try:
            async with self.slots:
                if self.saved >= self.max_total:
                    self.record(owner, "total_limit", http=response.status)
                    return
                length = response.headers.get("content-length", "")
                if length.isdigit() and int(length) > self.max_script:
                    self.record(owner, "script_limit", size=int(length), http=response.status)
                    return
                body = await asyncio.wait_for(response.body(), 10)
                if len(body) > self.max_script:
                    self.record(owner, "script_limit", size=len(body), http=response.status)
                elif self.saved + len(body) > self.max_total:
                    self.record(owner, "total_limit", size=len(body), http=response.status)
                else:
                    digest = hashlib.sha256(body).hexdigest()
                    (self.root / (digest + ".js")).write_bytes(body)
                    self.saved += len(body)
                    self.record(owner, "saved", digest=digest, size=len(body), http=response.status)
        except asyncio.CancelledError:
            self.record(owner, "capture_cancelled", http=response.status)
            raise
        except Exception as error:
            self.record(owner, "capture_error", http=response.status, detail=type(error).__name__ + ": " + str(error)[:200])

    async def finish(self):
        tasks = list(self.tasks)
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=12)
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        self.db.commit()
