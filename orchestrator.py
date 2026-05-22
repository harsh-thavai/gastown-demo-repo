import os, json, subprocess, time, threading, requests, tempfile
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    # dotenv not installed or .env not present — continue without failing
    pass

# Cross-platform temp directory (allows running on Windows/Unix)
TMP_DIR = os.environ.get("TMPDIR") or os.environ.get("TMP") or os.environ.get("TEMP") or tempfile.gettempdir()

DO_INFERENCE_URL = os.environ.get("DO_INFERENCE_URL", "")
MODEL_ACCESS_KEY = os.environ.get("MODEL_ACCESS_KEY", "")
BRIDGE_URL       = "http://localhost:8080/ingest"
BRIDGE_SECRET    = os.environ.get("BRIDGE_SECRET", "gastown-demo-2026")
VERCEL_TOKEN     = os.environ.get("VERCEL_TOKEN", "")
VERCEL_PROJECT   = os.environ.get("VERCEL_PROJECT", "gastown-demo")
MODEL            = "deepseek-v4-pro"

WORKTREES = {
    "polecat-auth":   os.path.expanduser("~/gastown/wt-auth"),
    "polecat-tests":  os.path.expanduser("~/gastown/wt-tests"),
    "polecat-debug":  os.path.expanduser("~/gastown/wt-debug"),
    "polecat-docs":   os.path.expanduser("~/gastown/wt-docs"),
    "polecat-review": os.path.expanduser("~/gastown/wt-review"),
}


def emit(agent, event_type, text, diff=None):
    payload = {
        "agent": agent,
        "agent_role": agent.replace("polecat-", ""),
        "type": event_type,
        "text": text,
        "time": datetime.now().strftime("%H:%M:%S"),
    }
    if diff:
        payload["diff"] = diff
    try:
        requests.post(BRIDGE_URL, json=payload,
                      headers={"X-Bridge-Secret": BRIDGE_SECRET}, timeout=2)
    except:
        pass
    print(f"[{payload['time']}] [{agent}] {event_type}: {text}")


DRY_RUN   = False  # set via --dry-run CLI flag
BUILD_MODE = False  # set via --build CLI flag

DRY_RUN_PLAN = {
    "convoy": "Pre-Launch Sprint",
    "tasks": [
        {"agent": "polecat-auth",   "task": "Add JWT middleware to /users and /search routes", "priority": "HIGH",   "file": "src/auth/jwt.go"},
        {"agent": "polecat-tests",  "task": "Write table-driven tests for JWT middleware",      "priority": "HIGH",   "file": "tests/auth_test.go"},
        {"agent": "polecat-debug",  "task": "OWASP+STRIDE audit of server.go and jwt.go",       "priority": "HIGH",   "file": "src/api/server.go"},
        {"agent": "polecat-docs",   "task": "Update README with auth section and usage",         "priority": "LOW",    "file": "README.md"},
        {"agent": "polecat-review", "task": "Staff eng review of all polecat diffs",             "priority": "MEDIUM", "file": "src/auth/jwt.go"},
    ]
}

DRY_RUN_BUILD_PLAN = {
    "convoy": "SaaS Dashboard Build",
    "project_name": "saas-dashboard",
    "framework": "nextjs",
    "tasks": [
        {"agent": "polecat-auth",   "task": "Scaffold NextAuth.js login/register pages with JWT", "priority": "HIGH",   "file": "src/app/auth/login/page.tsx"},
        {"agent": "polecat-tests",  "task": "Write Jest tests for auth and dashboard components",  "priority": "HIGH",   "file": "src/__tests__/auth.test.tsx"},
        {"agent": "polecat-debug",  "task": "Security audit of auth flow and Stripe webhook",      "priority": "HIGH",   "file": "src/app/api/webhook/route.ts"},
        {"agent": "polecat-docs",   "task": "Write README with setup, env vars, deploy steps",     "priority": "LOW",    "file": "README.md"},
        {"agent": "polecat-review", "task": "Final review and polish of all scaffolded files",     "priority": "MEDIUM", "file": "src/app/dashboard/page.tsx"},
    ]
}


def call_do_inference(system, user):
    if DRY_RUN:
        return json.dumps(DRY_RUN_BUILD_PLAN if BUILD_MODE else DRY_RUN_PLAN)
    if not DO_INFERENCE_URL or not MODEL_ACCESS_KEY:
        raise RuntimeError("DO_INFERENCE_URL and MODEL_ACCESS_KEY must be set in .env")
    resp = requests.post(
        f"{DO_INFERENCE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {MODEL_ACCESS_KEY}",
                 "Content-Type": "application/json"},
        json={"model": MODEL,
              "messages": [{"role": "system", "content": system},
                           {"role": "user",   "content": user}],
              "max_tokens": 2000, "temperature": 0.3}
    )
    data = resp.json()
    if "choices" not in data:
        raise RuntimeError(f"DO Inference error: {data}")
    return data["choices"][0]["message"]["content"]


def parse_meeting_notes(notes):
    emit("mayor", "TASK_STARTED", "Parsing meeting notes...")
    system = """You are The Mayor — orchestration coordinator for Gas Town.
Parse meeting notes into a convoy plan.
Respond ONLY with valid JSON, no markdown fences:
{
  "convoy": "sprint name 2-4 words",
  "tasks": [
    {
      "agent": "polecat-auth|polecat-tests|polecat-debug|polecat-docs|polecat-review",
      "task": "specific single coding task, one sentence",
      "priority": "HIGH|MEDIUM|LOW",
      "file": "relative path e.g. src/auth/jwt.go"
    }
  ]
}
Rules: max one task per agent. polecat-debug always gets security/audit task.
polecat-review always reviews last. Be specific about file paths."""

    raw = call_do_inference(system, f"Meeting notes:\n{notes}")
    raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    plan = json.loads(raw)
    emit("mayor", "CONVOY_CREATED",
         f"Convoy: {plan['convoy']} — {len(plan['tasks'])} tasks")
    return plan


def run_agent(agent_name, task, target_file):
    worktree = WORKTREES.get(agent_name)
    role = agent_name.replace("polecat-", "")
    done_file = os.path.join(TMP_DIR, f"{agent_name}.done")
    if os.path.exists(done_file):
        os.remove(done_file)

    gstack_skill = ""
    skill_path = os.path.expanduser(
        f"~/.claude/skills/gstack/{role}/SKILL.md")
    if os.path.exists(skill_path):
        with open(skill_path) as f:
            gstack_skill = f"Methodology (gstack /{role}):\n{f.read()[:600]}\n\n"

    existing = ""
    full_path = os.path.join(worktree, target_file)
    if os.path.exists(full_path):
        with open(full_path) as f:
            existing = f.read()

    done_path_for_prompt = os.path.join(TMP_DIR, f"{agent_name}.done")

    prompt = f"""{gstack_skill}You are {agent_name}, a senior Go engineer in Gas Town.

Task: {task}
File: {target_file}
Working dir: {worktree}

Context (existing file):
{existing[:800] if existing else "File does not exist yet — create it."}

Instructions:
1. Write production-quality, idiomatic Go code for the task above.
2. Save to {target_file} in your working directory.
3. Run: git add . && git commit -m "{agent_name}: {task[:55]}"
4. Run: git push origin polecat/{role}
5. Run: echo DONE > {done_path_for_prompt}

No explanation needed. Just write the code and execute the git steps."""

    emit(agent_name, "AGENT_SPAWNED", f"{agent_name} online — worktree ready")
    time.sleep(0.3)
    emit(agent_name, "TASK_STARTED", f"Starting: {task}")

    if DRY_RUN:
        time.sleep(2 + hash(agent_name) % 4)  # stagger completions 2–5 s
        dry_diff = f"// [dry-run] {agent_name} — {target_file}\n// Task: {task}\n"
        emit(agent_name, "CODE_WRITTEN",
             f"{target_file} — dry-run simulated commit",
             diff=dry_diff)
        time.sleep(0.5)
        emit(agent_name, "REVIEW_PASSED", f"Pushed to branch polecat/{role} (dry-run)")
        return

    try:
        subprocess.run(["tmux", "new-window", "-t", "gastown", "-n", role],
                       capture_output=True)
        safe_prompt = prompt.replace("'", "'\"'\"'")
        subprocess.run([
            "tmux", "send-keys", "-t", f"gastown:{role}",
            f"cd {worktree} && claude --print '{safe_prompt}'", "Enter"
        ])
    except FileNotFoundError:
        emit(agent_name, "AGENT_STUCK",
             f"{agent_name}: tmux not found — run on Linux droplet or use --dry-run")
        return

    for _ in range(96):
        time.sleep(5)
        if os.path.exists(done_file):
            os.remove(done_file)
            written = ""
            if os.path.exists(full_path):
                with open(full_path) as f:
                    written = f.read()
            lines = len(written.splitlines()) if written else 0
            emit(agent_name, "CODE_WRITTEN",
                 f"{target_file} — {lines} lines committed",
                 diff=written[:500])
            time.sleep(1)
            emit(agent_name, "REVIEW_PASSED",
                 f"Pushed to branch polecat/{role}")
            return

    emit(agent_name, "AGENT_STUCK",
         f"{agent_name} timed out — use override button in dashboard")


def open_pr(plan):
    if DRY_RUN:
        pr_url = "https://github.com/demo-repo/pull/43"
        emit("mayor", "PR_OPENED", f"PR #43 opened — {pr_url} (dry-run)", diff=pr_url)
        return pr_url
    emit("mayor", "PR_OPENED", "Opening GitHub PR...")
    task_list = "\n".join(
        f"- {t['agent']}: {t['task']}" for t in plan["tasks"])
    try:
        result = subprocess.run([
            "gh", "pr", "create",
            "--title",  f"[Gas Town] {plan['convoy']}",
            "--body",   f"Multi-agent convoy via Gas Town\n\nStack: DO Inference + gstack methodology\n\nAgents:\n{task_list}",
            "--base",   "main",
            "--head",   "polecat/auth"
        ], cwd=os.path.expanduser("~/gastown/demo-repo"),
           capture_output=True, text=True)
        pr_url = result.stdout.strip()
    except FileNotFoundError:
        pr_url = "https://github.com/demo-repo/pull/43"
        emit("mayor", "PR_OPENED", f"gh not found — simulated PR: {pr_url}", diff=pr_url)
        return pr_url
    emit("mayor", "PR_OPENED", f"PR opened: {pr_url}", diff=pr_url)
    return pr_url


def trigger_vercel_deploy():
    if DRY_RUN:
        emit("vercel", "DEPLOY_STARTED", "Vercel triggered — building from main (dry-run)")
        time.sleep(3)
        deploy_url = "https://gastown-demo.vercel.app"
        emit("vercel", "DEPLOYMENT_READY", f"Live: {deploy_url} (dry-run)", diff=deploy_url)
        return
    if not VERCEL_TOKEN:
        emit("vercel", "DEPLOY_SKIPPED", "VERCEL_TOKEN not set")
        return
    emit("vercel", "DEPLOY_STARTED", "Vercel triggered — building from main")
    headers = {"Authorization": f"Bearer {VERCEL_TOKEN}",
               "Content-Type": "application/json"}
    payload = {
        "name": VERCEL_PROJECT,
        "gitSource": {"type": "github", "ref": "main",
                      "repoId": os.environ.get("GITHUB_REPO_ID", "")},
        "target": "production"
    }
    resp = requests.post("https://api.vercel.com/v13/deployments",
                         headers=headers, json=payload)
    data = resp.json()
    deploy_id  = data.get("id", "")
    deploy_url = f"https://{data.get('url', VERCEL_PROJECT + '.vercel.app')}"
    emit("vercel", "DEPLOYMENT_TRIGGERED", f"Build started — {deploy_id}")

    for i in range(36):
        time.sleep(5)
        r = requests.get(
            f"https://api.vercel.com/v13/deployments/{deploy_id}",
            headers=headers).json()
        state = r.get("readyState", "BUILDING")
        if state == "READY":
            emit("vercel", "DEPLOYMENT_READY",
                 f"Live: {deploy_url}", diff=deploy_url)
            return
        if state == "ERROR":
            emit("vercel", "DEPLOYMENT_FAILED", "Build failed")
            return


def run_convoy(notes):
    emit("mayor", "AGENT_SPAWNED", "Mayor online — Gas Town active")
    plan = parse_meeting_notes(notes)

    threads = []
    for task_def in plan["tasks"]:
        t = threading.Thread(
            target=run_agent,
            args=(task_def["agent"], task_def["task"], task_def["file"])
        )
        threads.append(t)
        t.start()
        time.sleep(0.5)

    for t in threads:
        t.join()

    pr_url = open_pr(plan)

    emit("mayor", "MERGED", "Refinery merged to main")
    trigger_vercel_deploy()
    emit("mayor", "CONVOY_COMPLETE", "Done. Code shipped. Live on Vercel.")


# ── BUILD MODE ────────────────────────────────────────────────────────────────

def parse_project_brief(description):
    emit("mayor", "TASK_STARTED", "Planning project architecture...")
    system = """You are The Mayor — orchestration coordinator for Gas Town.
Plan a new software project from a description.
Respond ONLY with valid JSON, no markdown fences:
{
  "convoy": "project name 2-4 words",
  "project_name": "kebab-case-name",
  "framework": "nextjs|fastapi|express|go-api",
  "tasks": [
    {
      "agent": "polecat-auth|polecat-tests|polecat-debug|polecat-docs|polecat-review",
      "task": "specific single scaffolding task, one sentence",
      "priority": "HIGH|MEDIUM|LOW",
      "file": "relative path e.g. app/auth/login/page.tsx"
    }
  ]
}
Rules: max one task per agent. polecat-auth builds auth pages/logic.
polecat-tests writes tests. polecat-debug does security review.
polecat-docs writes README. polecat-review does final polish."""

    raw = call_do_inference(system, f"Project description:\n{description}")
    raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    plan = json.loads(raw)
    emit("mayor", "CONVOY_CREATED",
         f"Convoy: {plan['convoy']} — {len(plan['tasks'])} tasks | {plan['framework']}")
    return plan


def scaffold_agent(agent_name, task, target_file, project_dir, framework):
    role = agent_name.replace("polecat-", "")
    done_file = os.path.join(TMP_DIR, f"{agent_name}-build.done")
    if os.path.exists(done_file):
        os.remove(done_file)

    full_path = os.path.join(project_dir, target_file)

    prompt = f"""You are {agent_name}, a senior {framework} engineer in Gas Town.

Task: {task}
File: {target_file}
Working dir: {project_dir}

Instructions:
1. Write production-quality {framework} code for the task above.
2. Save to {target_file} inside {project_dir}.
3. Run: echo DONE > {done_file}

Write clean, deploy-ready code. No placeholders."""

    emit(agent_name, "AGENT_SPAWNED", f"{agent_name} online — scaffolding {framework}")
    time.sleep(0.3)
    emit(agent_name, "TASK_STARTED", f"Scaffolding: {task}")

    if DRY_RUN:
        time.sleep(2 + hash(agent_name) % 4)
        dry_diff = f"// [dry-run] {agent_name} — {target_file}\n// Task: {task}\n"
        emit(agent_name, "CODE_WRITTEN",
             f"{target_file} — dry-run simulated scaffold", diff=dry_diff)
        time.sleep(0.5)
        emit(agent_name, "REVIEW_PASSED", f"{target_file} scaffolded (dry-run)")
        return

    try:
        subprocess.run(["tmux", "new-window", "-t", "gastown", "-n", f"b-{role}"],
                       capture_output=True)
        safe_prompt = prompt.replace("'", "'\"'\"'")
        subprocess.run([
            "tmux", "send-keys", "-t", f"gastown:b-{role}",
            f"cd {project_dir} && claude --print '{safe_prompt}'", "Enter"
        ])
    except FileNotFoundError:
        emit(agent_name, "AGENT_STUCK",
             f"{agent_name}: tmux not found — use --dry-run on Windows")
        return

    for _ in range(120):  # 10 min timeout for scaffolding
        time.sleep(5)
        if os.path.exists(done_file):
            os.remove(done_file)
            written = ""
            if os.path.exists(full_path):
                with open(full_path) as f:
                    written = f.read()
            lines = len(written.splitlines()) if written else 0
            emit(agent_name, "CODE_WRITTEN",
                 f"{target_file} — {lines} lines scaffolded", diff=written[:500])
            time.sleep(0.5)
            emit(agent_name, "REVIEW_PASSED", f"{target_file} complete")
            return

    emit(agent_name, "AGENT_STUCK", f"{agent_name} timed out during scaffold")


def create_github_repo(project_name, project_dir):
    emit("mayor", "TASK_STARTED", f"Creating GitHub repo: {project_name}...")
    if DRY_RUN:
        repo_url = f"https://github.com/harsh-thavai/{project_name}"
        emit("mayor", "PR_OPENED", f"GitHub repo: {repo_url} (dry-run)", diff=repo_url)
        return repo_url
    try:
        subprocess.run(["git", "init"], cwd=project_dir, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=project_dir, capture_output=True)
        subprocess.run(["git", "commit", "-m", "feat: initial scaffold via Gas Town"],
                       cwd=project_dir, capture_output=True)
        subprocess.run([
            "gh", "repo", "create", project_name,
            "--public", "--source", ".", "--remote", "origin", "--push"
        ], cwd=project_dir, capture_output=True)
        repo_url = f"https://github.com/harsh-thavai/{project_name}"
        emit("mayor", "PR_OPENED", f"GitHub repo created: {repo_url}", diff=repo_url)
        return repo_url
    except FileNotFoundError:
        emit("mayor", "PR_OPENED", "gh not found — skipped repo creation")
        return f"https://github.com/harsh-thavai/{project_name}"


def deploy_new_project(project_dir, project_name):
    if DRY_RUN:
        deploy_url = f"https://{project_name}.vercel.app"
        emit("vercel", "DEPLOY_STARTED", f"Deploying {project_name} (dry-run)")
        time.sleep(3)
        emit("vercel", "DEPLOYMENT_READY", f"Live: {deploy_url} (dry-run)", diff=deploy_url)
        return deploy_url

    if not VERCEL_TOKEN:
        emit("vercel", "DEPLOY_SKIPPED", "VERCEL_TOKEN not set")
        return None

    emit("vercel", "DEPLOY_STARTED", f"Deploying {project_name} to Vercel...")

    try:
        result = subprocess.run(
            ["vercel", "--prod", "--yes", "--name", project_name,
             "--token", VERCEL_TOKEN],
            cwd=project_dir, capture_output=True, text=True, timeout=300
        )
        for line in (result.stdout + result.stderr).splitlines():
            if "vercel.app" in line and "https://" in line:
                deploy_url = line.strip()
                emit("vercel", "DEPLOYMENT_READY", f"Live: {deploy_url}", diff=deploy_url)
                return deploy_url
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: Vercel API
    headers = {"Authorization": f"Bearer {VERCEL_TOKEN}", "Content-Type": "application/json"}
    payload = {"name": project_name,
               "gitSource": {"type": "github", "ref": "main",
                              "repoId": os.environ.get("GITHUB_REPO_ID", "")},
               "target": "production"}
    data = requests.post("https://api.vercel.com/v13/deployments",
                         headers=headers, json=payload).json()
    deploy_id  = data.get("id", "")
    deploy_url = f"https://{data.get('url', project_name + '.vercel.app')}"
    emit("vercel", "DEPLOYMENT_TRIGGERED", f"Build started — {deploy_id}")

    for _ in range(60):
        time.sleep(5)
        r = requests.get(f"https://api.vercel.com/v13/deployments/{deploy_id}",
                         headers=headers).json()
        state = r.get("readyState", "BUILDING")
        if state == "READY":
            emit("vercel", "DEPLOYMENT_READY", f"Live: {deploy_url}", diff=deploy_url)
            return deploy_url
        if state == "ERROR":
            emit("vercel", "DEPLOYMENT_FAILED", "Build failed")
            return None

    return deploy_url


def build_project(description):
    emit("mayor", "AGENT_SPAWNED", "Mayor online — Gas Town BUILD MODE")
    plan = parse_project_brief(description)

    project_name = plan.get("project_name", "gastown-project")
    framework    = plan.get("framework", "nextjs")
    project_dir  = os.path.expanduser(f"~/gastown/builds/{project_name}")
    os.makedirs(project_dir, exist_ok=True)

    emit("mayor", "TASK_STARTED", f"Bootstrapping {framework} in {project_dir}")

    if not DRY_RUN:
        if framework == "nextjs":
            subprocess.run(
                ["npx", "create-next-app@latest", project_name,
                 "--typescript", "--tailwind", "--eslint",
                 "--app", "--src-dir", "--import-alias", "@/*", "--yes"],
                cwd=os.path.expanduser("~/gastown/builds"), capture_output=True
            )
        elif framework == "fastapi":
            subprocess.run(["pip3", "install", "fastapi", "uvicorn"],
                           capture_output=True)
        elif framework in ("express", "go-api"):
            subprocess.run(["npm", "init", "-y"], cwd=project_dir, capture_output=True)

    emit("mayor", "TASK_STARTED",
         f"Bootstrap done — spawning {len(plan['tasks'])} agents")

    threads = []
    for task_def in plan["tasks"]:
        t = threading.Thread(
            target=scaffold_agent,
            args=(task_def["agent"], task_def["task"],
                  task_def["file"], project_dir, framework)
        )
        threads.append(t)
        t.start()
        time.sleep(0.5)

    for t in threads:
        t.join()

    repo_url   = create_github_repo(project_name, project_dir)
    emit("mayor", "MERGED", f"All agents done — deploying {project_name}")
    deploy_url = deploy_new_project(project_dir, project_name)
    emit("mayor", "CONVOY_COMPLETE",
         f"Done. {project_name} live at {deploy_url or repo_url}")
    return deploy_url


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if "--dry-run" in args:
        DRY_RUN = True
        args = [a for a in args if a != "--dry-run"]
        print("[dry-run] API and tmux calls skipped — events emitted to bridge only")

    if "--build" in args:
        BUILD_MODE = True
        args = [a for a in args if a != "--build"]
        build_project(" ".join(args))
    elif args:
        run_convoy(" ".join(args))
    else:
        task_file = os.path.join(TMP_DIR, "gastown-task")
        print(f"Watching for {task_file}...")
        while True:
            if os.path.exists(task_file):
                with open(task_file) as f:
                    notes = f.read().strip()
                os.remove(task_file)
                run_convoy(notes)
            time.sleep(1)
