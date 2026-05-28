"""
Gas Town — FastAPI Bridge
=========================
Replaces bridge/main.go with pure Python.
Handles SSE streaming, event ingestion, task queuing, LLM health checks.

Run:  uvicorn api.main:app --host 0.0.0.0 --port 8000
"""
import asyncio
import json
import os
import queue as sync_queue
import random
import time
import threading
from datetime import datetime

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import requests as http_requests

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))
except Exception:
    pass

# ── config ────────────────────────────────────────────────────────────────────
BRIDGE_SECRET    = os.environ.get("BRIDGE_SECRET", "gastown-demo-2026")
DO_INFERENCE_URL = os.environ.get("DO_INFERENCE_URL", "").rstrip("/")
MODEL_ACCESS_KEY = os.environ.get("MODEL_ACCESS_KEY", "")
MODEL            = os.environ.get("MODEL", "deepseek-v4-pro")
TMP_DIR          = (
    os.environ.get("TMPDIR")
    or os.environ.get("TMP")
    or os.environ.get("TEMP")
    or "/tmp"
)

# ── app ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Gas Town Bridge", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── shared state ──────────────────────────────────────────────────────────────
events_log: list[dict] = []        # last 200 events
agent_states: dict     = {}
deploy_url: str        = ""
convoy_name: str       = ""
# Use thread-safe queues — avoids asyncio.Queue + call_soon_threadsafe race on Python 3.12
connected_queues: list[sync_queue.Queue] = []
_queues_lock = threading.Lock()

AGENT_META = {
    "mayor":          "Orchestrator",
    "polecat-auth":   "Auth Engineer",
    "polecat-coder":  "Core Coder",
    "polecat-tests":  "Test Engineer",
    "polecat-debug":  "Security Auditor",
    "polecat-docs":   "Documentation",
    "polecat-review": "Staff Engineer",
    "vercel":         "Deployment",
}

for _a in AGENT_META:
    agent_states[_a] = {
        "name":       _a,
        "role":       _a.replace("polecat-", ""),
        "status":     "idle",
        "last_task":  "",
        "last_seen":  "",
        "deploy_url": "",
    }


# ── broadcast ─────────────────────────────────────────────────────────────────
def _broadcast(event: dict):
    """Fully thread-safe: update state + push to every SSE client queue."""
    global deploy_url, convoy_name

    events_log.append(event)
    if len(events_log) > 200:
        events_log.pop(0)

    agent = event.get("agent", "")
    etype = event.get("type", "")

    if agent in agent_states:
        s = agent_states[agent]
        s["last_task"] = event.get("text", "")[:100]
        s["last_seen"] = event.get("time", datetime.now().strftime("%H:%M:%S"))
        if etype in ("AGENT_SPAWNED", "TASK_STARTED", "CODE_WRITTEN", "CSO_RUNNING"):
            s["status"] = "working"
        elif etype in (
            "REVIEW_PASSED", "AUDIT_DONE", "CONVOY_COMPLETE",
            "MERGED", "DEPLOYMENT_READY",
        ):
            s["status"] = "done"
        elif etype == "AGENT_STUCK":
            s["status"] = "stuck"

    if etype == "CONVOY_CREATED":
        convoy_name = event.get("text", "").split(" — ")[0].replace("Convoy: ", "")
    if etype == "DEPLOYMENT_READY":
        deploy_url = event.get("diff", "") or event.get("text", "")

    # threading.Queue.put_nowait is natively thread-safe — no event loop magic needed
    with _queues_lock:
        snapshot = list(connected_queues)
    for q in snapshot:
        try:
            q.put_nowait(event)
        except Exception:
            pass


# ── endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status":  "ok",
        "clients": len(connected_queues),
        "events":  len(events_log),
    }


@app.get("/llm-health")
def llm_health():
    """Ping the LLM inference API and report latency + status."""
    if not DO_INFERENCE_URL or not MODEL_ACCESS_KEY:
        return {
            "status":  "unconfigured",
            "message": "DO_INFERENCE_URL or MODEL_ACCESS_KEY not set in .env",
        }
    try:
        t0 = time.monotonic()
        resp = http_requests.post(
            f"{DO_INFERENCE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {MODEL_ACCESS_KEY}",
                "Content-Type":  "application/json",
            },
            json={
                "model":    MODEL,
                "messages": [{"role": "user", "content": "reply with the single word: pong"}],
                "max_tokens": 10,
            },
            timeout=15,
        )
        elapsed_ms = (time.monotonic() - t0) * 1000
        if resp.status_code == 200:
            data  = resp.json()
            reply = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return {
                "status":     "ok",
                "model":      MODEL,
                "latency_ms": round(elapsed_ms, 1),
                "reply":      reply.strip(),
            }
        return {
            "status":    "error",
            "http_code": resp.status_code,
            "message":   resp.text[:300],
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)[:300]}


@app.get("/state")
def state():
    return {
        "agents":      agent_states,
        "deploy_url":  deploy_url,
        "convoy":      convoy_name,
        "event_count": len(events_log),
        "clients":     len(connected_queues),
    }


@app.post("/ingest")
async def ingest(request: Request):
    """Orchestrator posts agent events here."""
    secret = request.headers.get("X-Bridge-Secret", "")
    if secret != BRIDGE_SECRET:
        raise HTTPException(status_code=403, detail="Invalid bridge secret")
    try:
        event = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not event.get("time"):
        event["time"] = datetime.now().strftime("%H:%M:%S")
    _broadcast(event)
    return {"ok": True}


@app.post("/task")
async def task(request: Request):
    """Browser sends standup notes → fix convoy."""
    body  = await request.json()
    notes = body.get("notes", "").strip()
    if not notes:
        raise HTTPException(status_code=400, detail="notes required")
    task_file = os.path.join(TMP_DIR, "gastown-task")
    with open(task_file, "w") as f:
        f.write(notes)
    return {"ok": True, "message": "Task queued — Mayor will parse shortly"}


@app.post("/build")
async def build_endpoint(request: Request):
    """Browser sends project description → build convoy."""
    body = await request.json()
    desc = body.get("description", body.get("notes", "")).strip()
    if not desc:
        raise HTTPException(status_code=400, detail="description required")
    build_file = os.path.join(TMP_DIR, "gastown-build")
    with open(build_file, "w") as f:
        f.write(desc)
    return {"ok": True, "message": "Build queued — Mayor will scaffold shortly"}


@app.get("/events")
async def events_stream(request: Request):
    """SSE endpoint — browser subscribes here for live agent events."""
    async def generator():
        q: sync_queue.Queue = sync_queue.Queue()
        with _queues_lock:
            connected_queues.append(q)
        loop = asyncio.get_event_loop()
        # Replay last 20 events for late-joiners
        for ev in list(events_log[-20:]):
            yield f"data: {json.dumps(ev)}\n\n"
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    # run_in_executor lets the blocking queue.get() wait in a thread
                    # without ever blocking the asyncio event loop
                    event = await loop.run_in_executor(
                        None, lambda: q.get(timeout=15.0)
                    )
                    yield f"data: {json.dumps(event)}\n\n"
                except sync_queue.Empty:
                    yield ": heartbeat\n\n"
        finally:
            with _queues_lock:
                if q in connected_queues:
                    connected_queues.remove(q)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":        "keep-alive",
        },
    )


@app.post("/demo/start")
async def demo_start(background_tasks: BackgroundTasks):
    """Run a simulated 6-agent convoy for demo / testing."""
    background_tasks.add_task(_run_demo)
    return {"ok": True, "message": "Demo sequence started"}


# ── demo simulation ───────────────────────────────────────────────────────────
def _run_demo():
    """Simulate a realistic 6-agent convoy with real-looking timing."""
    global deploy_url, convoy_name
    for s in agent_states.values():
        s["status"] = "idle"
        s["last_task"] = ""
    deploy_url = ""

    steps = [
        # delay, agent, event_type, text, diff
        (0.3,  "mayor",          "AGENT_SPAWNED",   "Mayor online — Gas Town active",                              None),
        (1.2,  "mayor",          "TASK_STARTED",    "Parsing standup notes...",                                    None),
        (2.5,  "mayor",          "CONVOY_CREATED",  "Convoy: Pre-Launch Sprint — 6 tasks",                        None),
        (0.4,  "polecat-auth",   "AGENT_SPAWNED",   "polecat-auth online — worktree ready",                       None),
        (0.15, "polecat-coder",  "AGENT_SPAWNED",   "polecat-coder online — worktree ready",                      None),
        (0.15, "polecat-tests",  "AGENT_SPAWNED",   "polecat-tests online — worktree ready",                      None),
        (0.15, "polecat-debug",  "AGENT_SPAWNED",   "polecat-debug online — OWASP+STRIDE ready",                  None),
        (0.15, "polecat-docs",   "AGENT_SPAWNED",   "polecat-docs online — worktree ready",                       None),
        (0.3,  "polecat-auth",   "TASK_STARTED",    "Add JWT middleware to /users and /search routes",            None),
        (0.1,  "polecat-coder",  "TASK_STARTED",    "Implement core user service with CRUD operations",           None),
        (0.1,  "polecat-tests",  "TASK_STARTED",    "Write table-driven tests for JWT middleware",                None),
        (0.1,  "polecat-debug",  "TASK_STARTED",    "OWASP+STRIDE audit of auth flow and endpoints",              None),
        (0.1,  "polecat-docs",   "TASK_STARTED",    "Update README with auth section and API reference",          None),
        (3.2,  "polecat-coder",  "CODE_WRITTEN",    "src/services/user_service.py — 210 lines written",           None),
        (1.5,  "polecat-auth",   "CODE_WRITTEN",    "src/auth/jwt.py — 142 lines written",                       None),
        (1.2,  "polecat-tests",  "CODE_WRITTEN",    "tests/test_auth.py — 89 lines written",                     None),
        (0.9,  "polecat-docs",   "CODE_WRITTEN",    "README.md — auth section added, 47 lines",                  None),
        (1.1,  "polecat-debug",  "AUDIT_DONE",      "OWASP audit — 0 critical, 2 medium fixed",                  None),
        (0.5,  "polecat-review", "AGENT_SPAWNED",   "polecat-review online — reviewing all 6 diffs",             None),
        (2.0,  "polecat-review", "TASK_STARTED",    "Staff eng review of all polecat diffs",                     None),
        (0.8,  "polecat-auth",   "REVIEW_PASSED",   "Pushed to branch polecat/auth",                             None),
        (0.3,  "polecat-coder",  "REVIEW_PASSED",   "Pushed to branch polecat/coder",                            None),
        (0.3,  "polecat-tests",  "REVIEW_PASSED",   "Pushed to branch polecat/tests",                            None),
        (0.3,  "polecat-docs",   "REVIEW_PASSED",   "Pushed to branch polecat/docs",                             None),
        (0.3,  "polecat-debug",  "REVIEW_PASSED",   "Pushed to branch polecat/debug",                            None),
        (1.5,  "polecat-review", "REVIEW_PASSED",   "Staff review complete — LGTM on all 6",                     None),
        (0.5,  "mayor",          "PR_OPENED",       "PR #44 opened: [Gas Town] Pre-Launch Sprint",               "https://github.com/harsh-thavai/gastown-demo-repo/pulls"),
        (0.8,  "mayor",          "MERGED",          "Merged to main — 6 agents, 584 lines shipped",              None),
        (0.5,  "vercel",         "DEPLOY_STARTED",  "Triggering Vercel deployment...",                            None),
        (4.5,  "vercel",         "DEPLOYMENT_READY","Live: https://gastown-demo.vercel.app",                      "https://gastown-demo.vercel.app"),
        (0.5,  "mayor",          "CONVOY_COMPLETE", "Done. 6 agents, code is live on Vercel.",                   None),
    ]

    for delay, agent, etype, text, diff in steps:
        time.sleep(delay + random.uniform(0, 0.3))
        event = {
            "agent":      agent,
            "agent_role": agent.replace("polecat-", ""),
            "type":       etype,
            "text":       text,
            "time":       datetime.now().strftime("%H:%M:%S"),
        }
        if diff:
            event["diff"] = diff
        _broadcast(event)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
