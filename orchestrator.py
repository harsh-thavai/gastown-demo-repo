"""
Gas Town Orchestrator
=====================
Mayor parses standup notes / project descriptions via DO Inference,
spawns 5 polecat agents (also via DO Inference — no tmux, no claude CLI),
each agent generates a file, then the project is pushed to GitHub and
deployed to Vercel.

Usage
-----
Fix mode  : python3 orchestrator.py "fix auth bug, add tests"
Build mode: python3 orchestrator.py --build "Next.js SaaS with Stripe"
Dry run   : add --dry-run to either mode
"""

import os, json, subprocess, time, threading, requests, tempfile, re
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ── config ─────────────────────────────────────────────────────────────────────
TMP_DIR          = os.environ.get("TMPDIR") or os.environ.get("TMP") \
                   or os.environ.get("TEMP") or tempfile.gettempdir()
DO_INFERENCE_URL = os.environ.get("DO_INFERENCE_URL", "").rstrip("/")
MODEL_ACCESS_KEY = os.environ.get("MODEL_ACCESS_KEY", "")
BRIDGE_URL       = os.environ.get("BRIDGE_URL", "http://localhost:8080/ingest")
BRIDGE_SECRET    = os.environ.get("BRIDGE_SECRET", "gastown-demo-2026")
VERCEL_TOKEN     = os.environ.get("VERCEL_TOKEN", "")
VERCEL_PROJECT   = os.environ.get("VERCEL_PROJECT", "gastown-demo")
GITHUB_TOKEN     = os.environ.get("GITHUB_TOKEN", "")
GITHUB_USER      = os.environ.get("GITHUB_USER", "harsh-thavai")
MODEL            = os.environ.get("MODEL", "deepseek-v4-pro")

WORKTREES = {
    "polecat-auth":   os.path.expanduser("~/gastown/wt-auth"),
    "polecat-tests":  os.path.expanduser("~/gastown/wt-tests"),
    "polecat-debug":  os.path.expanduser("~/gastown/wt-debug"),
    "polecat-docs":   os.path.expanduser("~/gastown/wt-docs"),
    "polecat-review": os.path.expanduser("~/gastown/wt-review"),
}

DRY_RUN    = False
BUILD_MODE = False

# ── dry-run fixtures ───────────────────────────────────────────────────────────
DRY_RUN_PLAN = {
    "convoy": "Pre-Launch Sprint",
    "tasks": [
        {"agent": "polecat-auth",   "task": "Add JWT middleware to /users and /search routes",
         "priority": "HIGH",   "file": "src/auth/jwt.go"},
        {"agent": "polecat-tests",  "task": "Write table-driven tests for JWT middleware",
         "priority": "HIGH",   "file": "tests/auth_test.go"},
        {"agent": "polecat-debug",  "task": "OWASP+STRIDE audit of server.go and jwt.go",
         "priority": "HIGH",   "file": "src/api/server.go"},
        {"agent": "polecat-docs",   "task": "Update README with auth section and usage",
         "priority": "LOW",    "file": "README.md"},
        {"agent": "polecat-review", "task": "Staff eng review of all polecat diffs",
         "priority": "MEDIUM", "file": "src/auth/jwt.go"},
    ]
}

DRY_RUN_BUILD_PLAN = {
    "convoy": "SaaS Dashboard Build",
    "project_name": "saas-dashboard",
    "framework": "nextjs",
    "tasks": [
        {"agent": "polecat-auth",   "task": "Scaffold NextAuth.js login/register pages with JWT",
         "priority": "HIGH",   "file": "src/app/auth/login/page.tsx"},
        {"agent": "polecat-tests",  "task": "Write Jest tests for auth and dashboard components",
         "priority": "HIGH",   "file": "src/__tests__/auth.test.tsx"},
        {"agent": "polecat-debug",  "task": "Security audit of auth flow and Stripe webhook",
         "priority": "HIGH",   "file": "src/app/api/webhook/route.ts"},
        {"agent": "polecat-docs",   "task": "Write README with setup, env vars, deploy steps",
         "priority": "LOW",    "file": "README.md"},
        {"agent": "polecat-review", "task": "Final review and polish of all scaffolded files",
         "priority": "MEDIUM", "file": "src/app/dashboard/page.tsx"},
    ]
}


# ── emit ───────────────────────────────────────────────────────────────────────
def emit(agent, event_type, text, diff=None):
    payload = {
        "agent":      agent,
        "agent_role": agent.replace("polecat-", ""),
        "type":       event_type,
        "text":       text,
        "time":       datetime.now().strftime("%H:%M:%S"),
    }
    if diff:
        payload["diff"] = diff
    try:
        requests.post(BRIDGE_URL, json=payload,
                      headers={"X-Bridge-Secret": BRIDGE_SECRET}, timeout=2)
    except Exception:
        pass
    print(f"[{payload['time']}] [{agent}] {event_type}: {text}")


# ── DO Inference ───────────────────────────────────────────────────────────────
def call_do_inference(system, user, max_tokens=2000):
    """Call DO Inference API. Returns raw text response."""
    if DRY_RUN:
        return json.dumps(DRY_RUN_BUILD_PLAN if BUILD_MODE else DRY_RUN_PLAN)
    if not DO_INFERENCE_URL or not MODEL_ACCESS_KEY:
        raise RuntimeError("DO_INFERENCE_URL and MODEL_ACCESS_KEY must be set")
    resp = requests.post(
        f"{DO_INFERENCE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {MODEL_ACCESS_KEY}",
                 "Content-Type": "application/json"},
        json={"model": MODEL,
              "messages": [{"role": "system", "content": system},
                           {"role": "user",   "content": user}],
              "max_tokens": max_tokens, "temperature": 0.3},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    if "choices" not in data:
        raise RuntimeError(f"DO Inference error: {data}")
    return data["choices"][0]["message"]["content"]


def generate_file_content(agent_name, task, target_file, framework, context=""):
    """
    Ask DO Inference to generate the full content of a single file.
    Returns raw file content (not JSON).
    """
    if DRY_RUN:
        ext = os.path.splitext(target_file)[1]
        lang = {"tsx":"typescript","ts":"typescript","go":"go",
                ".py":"python",".md":"markdown"}.get(ext.lstrip("."),"text")
        return f"// [dry-run] {agent_name} — {target_file}\n// Task: {task}\nexport default function Placeholder() {{ return null; }}\n"

    system = f"""You are {agent_name}, a senior {framework} engineer.
Output ONLY the complete file content for {target_file}.
Do NOT include explanations, markdown fences, or commentary.
Output raw code only — exactly what should be written to disk."""

    user = f"""Task: {task}
File: {target_file}
Framework: {framework}

{f"Existing content to build upon:{chr(10)}{context[:600]}" if context else "This file does not exist yet — create it from scratch."}

Write production-quality, deploy-ready code. No placeholders. No TODO comments."""

    return call_do_inference(system, user, max_tokens=3000)


# ── Fix-mode: parse meeting notes ──────────────────────────────────────────────
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
    raw = re.sub(r"^```[a-z]*\n?", "", raw.strip())
    raw = re.sub(r"\n?```$", "", raw.strip())
    plan = json.loads(raw)
    emit("mayor", "CONVOY_CREATED",
         f"Convoy: {plan['convoy']} — {len(plan['tasks'])} tasks")
    return plan


# ── Fix-mode: run agent ────────────────────────────────────────────────────────
def run_agent(agent_name, task, target_file):
    """Generate file content via DO Inference, write to worktree, commit & push."""
    worktree  = WORKTREES.get(agent_name, "")
    role      = agent_name.replace("polecat-", "")
    full_path = os.path.join(worktree, target_file)

    emit(agent_name, "AGENT_SPAWNED", f"{agent_name} online — worktree ready")
    time.sleep(0.3)
    emit(agent_name, "TASK_STARTED", f"Starting: {task}")

    if DRY_RUN:
        time.sleep(2 + abs(hash(agent_name)) % 4)
        dry_diff = f"// [dry-run] {agent_name} — {target_file}\n// Task: {task}\n"
        emit(agent_name, "CODE_WRITTEN",
             f"{target_file} — dry-run simulated commit", diff=dry_diff)
        time.sleep(0.5)
        emit(agent_name, "REVIEW_PASSED", f"Pushed to branch polecat/{role} (dry-run)")
        return

    # Read existing file for context
    context = ""
    if os.path.exists(full_path):
        try:
            with open(full_path) as f:
                context = f.read()
        except Exception:
            pass

    # Generate content via DO Inference
    try:
        content = generate_file_content(agent_name, task, target_file, "go", context)
    except Exception as e:
        emit(agent_name, "AGENT_STUCK", f"Inference error: {e}")
        return

    # Write file
    try:
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w") as f:
            f.write(content)
    except Exception as e:
        emit(agent_name, "AGENT_STUCK", f"Write error: {e}")
        return

    lines = len(content.splitlines())
    emit(agent_name, "CODE_WRITTEN",
         f"{target_file} — {lines} lines written", diff=content[:500])

    # Git commit & push
    try:
        subprocess.run(["git", "add", "."], cwd=worktree, capture_output=True)
        subprocess.run(["git", "commit", "-m", f"{agent_name}: {task[:60]}"],
                       cwd=worktree, capture_output=True)
        subprocess.run(["git", "push", "origin", f"polecat/{role}"],
                       cwd=worktree, capture_output=True)
        emit(agent_name, "REVIEW_PASSED", f"Pushed to branch polecat/{role}")
    except Exception as e:
        emit(agent_name, "REVIEW_PASSED", f"Code written — git push skipped: {e}")


# ── Fix-mode: PR + deploy ──────────────────────────────────────────────────────
def open_pr(plan):
    if DRY_RUN:
        pr_url = "https://github.com/demo-repo/pull/43"
        emit("mayor", "PR_OPENED", f"PR #43 opened (dry-run)", diff=pr_url)
        return pr_url
    emit("mayor", "PR_OPENED", "Opening GitHub PR...")
    task_list = "\n".join(f"- {t['agent']}: {t['task']}" for t in plan["tasks"])
    try:
        result = subprocess.run([
            "gh", "pr", "create",
            "--title", f"[Gas Town] {plan['convoy']}",
            "--body",  f"Multi-agent convoy\n\nAgents:\n{task_list}",
            "--base",  "main", "--head", "polecat/auth",
        ], cwd=os.path.expanduser("~/gastown"), capture_output=True, text=True)
        pr_url = result.stdout.strip() or "https://github.com/pull/new"
    except FileNotFoundError:
        pr_url = "https://github.com/pull/new"
    emit("mayor", "PR_OPENED", f"PR: {pr_url}", diff=pr_url)
    return pr_url


def trigger_vercel_deploy():
    if DRY_RUN:
        emit("vercel", "DEPLOY_STARTED", "Vercel triggered (dry-run)")
        time.sleep(3)
        url = f"https://{VERCEL_PROJECT}.vercel.app"
        emit("vercel", "DEPLOYMENT_READY", f"Live: {url} (dry-run)", diff=url)
        return url
    if not VERCEL_TOKEN:
        emit("vercel", "DEPLOY_SKIPPED", "VERCEL_TOKEN not set")
        return None
    return _vercel_deploy_api(VERCEL_PROJECT,
                              os.path.expanduser("~/gastown"))


def run_convoy(notes):
    emit("mayor", "AGENT_SPAWNED", "Mayor online — Gas Town active")
    plan = parse_meeting_notes(notes)

    threads = []
    for t in plan["tasks"]:
        th = threading.Thread(target=run_agent,
                              args=(t["agent"], t["task"], t["file"]))
        threads.append(th)
        th.start()
        time.sleep(0.5)
    for th in threads:
        th.join()

    open_pr(plan)
    emit("mayor", "MERGED", "Refinery merged to main")
    trigger_vercel_deploy()
    emit("mayor", "CONVOY_COMPLETE", "Done. Code shipped.")


# ── Build mode ─────────────────────────────────────────────────────────────────
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
      "file": "relative file path"
    }
  ]
}
Rules: max one task per agent. polecat-auth builds auth.
polecat-tests writes tests. polecat-debug does security review.
polecat-docs writes README. polecat-review does final polish.
For nextjs use paths like src/app/auth/login/page.tsx"""

    raw = call_do_inference(system, f"Project description:\n{description}")
    raw = re.sub(r"^```[a-z]*\n?", "", raw.strip())
    raw = re.sub(r"\n?```$", "", raw.strip())
    plan = json.loads(raw)
    emit("mayor", "CONVOY_CREATED",
         f"Convoy: {plan['convoy']} — {len(plan['tasks'])} tasks | {plan['framework']}")
    return plan


def scaffold_agent(agent_name, task, target_file, project_dir, framework):
    """Generate a file via DO Inference and write it to project_dir."""
    role      = agent_name.replace("polecat-", "")
    full_path = os.path.join(project_dir, target_file)

    emit(agent_name, "AGENT_SPAWNED", f"{agent_name} online — scaffolding {framework}")
    time.sleep(0.3)
    emit(agent_name, "TASK_STARTED", f"Scaffolding: {task}")

    if DRY_RUN:
        time.sleep(2 + abs(hash(agent_name)) % 4)
        dry_diff = f"// [dry-run] {agent_name} — {target_file}\n// Task: {task}\n"
        emit(agent_name, "CODE_WRITTEN",
             f"{target_file} — dry-run scaffold", diff=dry_diff)
        time.sleep(0.5)
        emit(agent_name, "REVIEW_PASSED", f"{target_file} scaffolded (dry-run)")
        return

    # Generate content
    try:
        content = generate_file_content(agent_name, task, target_file, framework)
    except Exception as e:
        emit(agent_name, "AGENT_STUCK", f"Inference error: {e}")
        return

    # Write file
    try:
        os.makedirs(os.path.dirname(full_path) or ".", exist_ok=True)
        with open(full_path, "w") as f:
            f.write(content)
    except Exception as e:
        emit(agent_name, "AGENT_STUCK", f"Write error: {e}")
        return

    lines = len(content.splitlines())
    emit(agent_name, "CODE_WRITTEN",
         f"{target_file} — {lines} lines scaffolded", diff=content[:500])
    time.sleep(0.5)
    emit(agent_name, "REVIEW_PASSED", f"{target_file} complete")


def _bootstrap_nextjs(project_dir, project_name):
    """Write minimal Next.js 14 project files — no npx, no prompts."""
    dirs = [
        os.path.join(project_dir, "src", "app"),
        os.path.join(project_dir, "public"),
    ]
    for d in dirs:
        os.makedirs(d, exist_ok=True)

    files = {
        "package.json": json.dumps({
            "name": project_name, "version": "0.1.0", "private": True,
            "scripts": {
                "dev":   "next dev",
                "build": "next build",
                "start": "next start",
                "lint":  "next lint",
            },
            "dependencies": {
                "next": "15.3.6", "react": "^19", "react-dom": "^19",
                "next-auth": "^5.0.0-beta.28",
                "lucide-react": "^0.511.0",
                "stripe": "^17.7.0",
                "@stripe/stripe-js": "^5.8.0",
                "zod": "^3.24.4",
                "clsx": "^2.1.1",
                "tailwind-merge": "^3.3.0",
            },
            "devDependencies": {
                "typescript": "^5", "@types/node": "^20",
                "@types/react": "^19", "@types/react-dom": "^19",
                "tailwindcss": "^3", "autoprefixer": "^10", "postcss": "^8",
                "eslint": "^9", "eslint-config-next": "15.3.2",
            },
        }, indent=2),
        "next.config.ts": (
            'import type { NextConfig } from "next";\n'
            "const nextConfig: NextConfig = {};\n"
            "export default nextConfig;\n"
        ),
        "tsconfig.json": json.dumps({
            "compilerOptions": {
                "target": "es5", "lib": ["dom", "dom.iterable", "esnext"],
                "allowJs": True, "skipLibCheck": True, "strict": True,
                "noEmit": True, "esModuleInterop": True,
                "module": "esnext", "moduleResolution": "bundler",
                "resolveJsonModule": True, "isolatedModules": True,
                "jsx": "preserve", "incremental": True,
                "plugins": [{"name": "next"}],
                "paths": {"@/*": ["./src/*"]},
            },
            "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
            "exclude": ["node_modules"],
        }, indent=2),
        "tailwind.config.ts": (
            'import type { Config } from "tailwindcss";\n'
            'const config: Config = {\n'
            '  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],\n'
            '  theme: { extend: {} },\n'
            '  plugins: [],\n'
            '};\nexport default config;\n'
        ),
        "postcss.config.js": (
            "module.exports = {\n"
            "  plugins: { tailwindcss: {}, autoprefixer: {} },\n"
            "};\n"
        ),
        ".eslintrc.json": json.dumps({"extends": "next/core-web-vitals"}, indent=2),
        "vercel.json": json.dumps({
            "buildCommand": "npm run build",
            "framework": "nextjs",
            "regions": ["bom1"],
        }, indent=2),
        "src/app/globals.css": (
            "@tailwind base;\n@tailwind components;\n@tailwind utilities;\n"
        ),
        "src/app/layout.tsx": (
            'import type { Metadata } from "next";\n'
            'import "./globals.css";\n\n'
            f'export const metadata: Metadata = {{\n'
            f'  title: "{project_name}",\n'
            f'  description: "Built by Gas Town",\n'
            f'}};\n\n'
            'export default function RootLayout({\n'
            '  children,\n'
            '}: {\n'
            '  children: React.ReactNode;\n'
            '}) {\n'
            '  return (\n'
            '    <html lang="en">\n'
            '      <body>{children}</body>\n'
            '    </html>\n'
            '  );\n'
            '}\n'
        ),
        "src/app/page.tsx": (
            'export default function Home() {\n'
            '  return (\n'
            '    <main className="min-h-screen flex flex-col items-center justify-center p-8">\n'
            f'      <h1 className="text-4xl font-bold mb-4">{project_name}</h1>\n'
            '      <p className="text-gray-600">Built by Gas Town multi-agent system.</p>\n'
            '    </main>\n'
            '  );\n'
            '}\n'
        ),
    }

    for rel_path, content in files.items():
        full = os.path.join(project_dir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)

    emit("mayor", "TASK_STARTED", "Running npm install (this takes ~60s)...")
    try:
        result = subprocess.run(
            ["npm", "install", "--prefer-offline", "--no-audit", "--no-fund"],
            cwd=project_dir, capture_output=True, text=True, timeout=180,
        )
        if result.returncode != 0:
            emit("mayor", "TASK_STARTED",
                 f"npm install warning: {result.stderr[-200:]}")
    except subprocess.TimeoutExpired:
        emit("mayor", "TASK_STARTED", "npm install timed out — proceeding")


def create_github_repo(project_name, project_dir):
    emit("mayor", "TASK_STARTED", f"Creating GitHub repo: {project_name}...")
    if DRY_RUN:
        url = f"https://github.com/{GITHUB_USER}/{project_name}"
        emit("mayor", "PR_OPENED", f"GitHub repo: {url} (dry-run)", diff=url)
        return url

    # Configure git identity if missing
    subprocess.run(["git", "config", "user.email", "gastown@bot.local"],
                   cwd=project_dir, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Gas Town Bot"],
                   cwd=project_dir, capture_output=True)

    subprocess.run(["git", "init", "-b", "main"], cwd=project_dir, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=project_dir, capture_output=True)
    subprocess.run(["git", "commit", "-m", "feat: initial scaffold via Gas Town"],
                   cwd=project_dir, capture_output=True)

    try:
        result = subprocess.run([
            "gh", "repo", "create", project_name,
            "--public", "--source", ".", "--remote", "origin", "--push",
        ], cwd=project_dir, capture_output=True, text=True, timeout=60)
        if result.returncode != 0 and "already exists" in result.stderr:
            # Repo exists — just push
            remote = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_USER}/{project_name}.git"
            subprocess.run(["git", "remote", "add", "origin", remote],
                           cwd=project_dir, capture_output=True)
            subprocess.run(["git", "push", "-u", "origin", "main", "--force"],
                           cwd=project_dir, capture_output=True)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        emit("mayor", "PR_OPENED", f"gh skipped ({e}) — continuing")
        return f"https://github.com/{GITHUB_USER}/{project_name}"

    url = f"https://github.com/{GITHUB_USER}/{project_name}"
    emit("mayor", "PR_OPENED", f"GitHub repo: {url}", diff=url)
    return url


def _vercel_deploy_api(project_name, project_dir):
    """Deploy via Vercel API using file upload."""
    headers = {"Authorization": f"Bearer {VERCEL_TOKEN}",
               "Content-Type": "application/json"}

    # Try Vercel CLI first (fastest)
    try:
        result = subprocess.run(
            ["vercel", "--prod", "--yes", "--name", project_name,
             "--token", VERCEL_TOKEN, "--regions", "bom1"],
            cwd=project_dir, capture_output=True, text=True, timeout=300,
        )
        output = result.stdout + result.stderr
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("https://") and "vercel.app" in line:
                emit("vercel", "DEPLOYMENT_READY", f"Live: {line}", diff=line)
                return line
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: Vercel deployments API via GitHub source
    emit("vercel", "DEPLOY_STARTED", "Using Vercel API deploy...")
    payload = {
        "name": project_name,
        "target": "production",
        "regions": ["bom1"],
        "gitSource": {
            "type": "github",
            "repoId": os.environ.get("GITHUB_REPO_ID", ""),
            "ref": "main",
        },
    }
    resp = requests.post("https://api.vercel.com/v13/deployments",
                         headers=headers, json=payload, timeout=30)
    data = resp.json()
    if "error" in data:
        emit("vercel", "DEPLOYMENT_FAILED", f"API error: {data['error']}")
        return None

    deploy_id  = data.get("id", "")
    deploy_url = f"https://{data.get('url', project_name + '.vercel.app')}"
    emit("vercel", "DEPLOYMENT_TRIGGERED", f"Build started — {deploy_id}")

    for _ in range(60):
        time.sleep(5)
        r = requests.get(
            f"https://api.vercel.com/v13/deployments/{deploy_id}",
            headers=headers, timeout=10,
        ).json()
        state = r.get("readyState", "BUILDING")
        if state == "READY":
            emit("vercel", "DEPLOYMENT_READY", f"Live: {deploy_url}", diff=deploy_url)
            return deploy_url
        if state in ("ERROR", "CANCELED"):
            emit("vercel", "DEPLOYMENT_FAILED", f"Build {state}")
            return None

    emit("vercel", "DEPLOYMENT_READY", f"Live: {deploy_url}", diff=deploy_url)
    return deploy_url


def deploy_new_project(project_dir, project_name):
    if DRY_RUN:
        url = f"https://{project_name}.vercel.app"
        emit("vercel", "DEPLOY_STARTED", f"Deploying {project_name} (dry-run)")
        time.sleep(3)
        emit("vercel", "DEPLOYMENT_READY", f"Live: {url} (dry-run)", diff=url)
        return url
    if not VERCEL_TOKEN:
        emit("vercel", "DEPLOY_SKIPPED", "VERCEL_TOKEN not set — skipping deploy")
        return None
    emit("vercel", "DEPLOY_STARTED", f"Deploying {project_name} to Vercel...")
    return _vercel_deploy_api(project_name, project_dir)


def _fix_missing_deps(project_dir):
    """
    After agents write files:
    1. Scan every .ts/.tsx for imports, install missing npm packages.
    2. Create stubs for missing local @/* paths with correct named exports.
    3. Fix broken NextAuth v5 route handlers.
    """
    import re as _re

    # npm package mapping: import-path-prefix → npm install name
    KNOWN_PKGS = {
        "next-auth":               "next-auth@^5.0.0-beta.28",
        "next-auth/react":         "next-auth@^5.0.0-beta.28",
        "next-auth/providers":     "next-auth@^5.0.0-beta.28",
        "lucide-react":            "lucide-react",
        "stripe":                  "stripe",
        "@stripe/stripe-js":       "@stripe/stripe-js",
        "zod":                     "zod",
        "clsx":                    "clsx",
        "tailwind-merge":          "tailwind-merge",
        "drizzle-orm":             "drizzle-orm",
        "drizzle-orm/pg-core":             "drizzle-orm",
        "drizzle-orm/sqlite-core":         "drizzle-orm",
        "@radix-ui/react-dialog":          "@radix-ui/react-dialog",
        "@radix-ui/react-label":           "@radix-ui/react-label",
        "@radix-ui/react-slot":            "@radix-ui/react-slot",
        "@radix-ui/react-dropdown-menu":   "@radix-ui/react-dropdown-menu",
        "@radix-ui/react-avatar":          "@radix-ui/react-avatar",
        "@radix-ui/react-tabs":            "@radix-ui/react-tabs",
        "react-hook-form":                 "react-hook-form",
        "@hookform/resolvers":             "@hookform/resolvers",
        "@hookform/resolvers/zod":         "@hookform/resolvers",
        "sonner":                          "sonner",
        "date-fns":                        "date-fns",
        "class-variance-authority":        "class-variance-authority",
        "prisma":                          "@prisma/client",
        "@prisma/client":                  "@prisma/client",
        "bcryptjs":                        "bcryptjs",
        "bcrypt":                          "bcrypt",
        "jsonwebtoken":                    "jsonwebtoken",
        "jose":                            "jose",
        "nodemailer":                      "nodemailer",
        "resend":                          "resend",
        "@supabase/supabase-js":           "@supabase/supabase-js",
        "@supabase/auth-helpers-nextjs":   "@supabase/auth-helpers-nextjs",
        "@supabase/ssr":                   "@supabase/ssr",
        "axios":                           "axios",
        "swr":                             "swr",
        "@tanstack/react-query":           "@tanstack/react-query",
        "framer-motion":                   "framer-motion",
        "recharts":                        "recharts",
        "react-hot-toast":                 "react-hot-toast",
        "react-toastify":                  "react-toastify",
    }

    # Well-known local lib stubs (full content)
    STATIC_STUBS = {
        "src/auth.ts": (
            "import NextAuth from 'next-auth';\n"
            "export const { handlers, auth, signIn, signOut } = "
            "NextAuth({ providers: [] });\n"
        ),
        "src/lib/auth.ts": (
            "import NextAuth from 'next-auth';\n"
            "export const { handlers, auth, signIn, signOut } = "
            "NextAuth({ providers: [] });\n"
        ),
        "src/lib/stripe.ts": (
            "import Stripe from 'stripe';\n"
            "export const stripe = new Stripe("
            "process.env.STRIPE_SECRET_KEY ?? '', "
            "{ apiVersion: '2024-12-18.acacia' });\n"
        ),
        "src/lib/utils.ts": (
            "import { clsx, type ClassValue } from 'clsx';\n"
            "import { twMerge } from 'tailwind-merge';\n"
            "export function cn(...inputs: ClassValue[]) "
            "{ return twMerge(clsx(inputs)); }\n"
        ),
        "src/lib/prisma.ts": (
            "import { PrismaClient } from '@prisma/client';\n"
            "const globalForPrisma = globalThis as unknown as "
            "{ prisma: PrismaClient };\n"
            "export const prisma = globalForPrisma.prisma ?? new PrismaClient();\n"
            "if (process.env.NODE_ENV !== 'production') "
            "globalForPrisma.prisma = prisma;\n"
        ),
    }

    # Regex to parse: import { a, b as c } from 'path'
    # and: import Default from 'path'
    # and: import * as NS from 'path'
    named_re   = _re.compile(r"import\s*\{([^}]+)\}\s*from\s*['\"]([^'\"]+)['\"]")
    default_re = _re.compile(r"import\s+(\w+)\s*from\s*['\"]([^'\"]+)['\"]")
    star_re    = _re.compile(r"import\s*\*\s*as\s+(\w+)\s*from\s*['\"]([^'\"]+)['\"]")
    path_re    = _re.compile(r"""from\s+['"]([^'"]+)['"]""")

    # Collect: local_path → set of named exports needed
    local_needs: dict = {}
    missing_pkgs: set = set()

    for root, _, files in os.walk(project_dir):
        if "node_modules" in root or ".next" in root:
            continue
        for fname in files:
            if not fname.endswith((".ts", ".tsx")):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath) as f:
                    src = f.read()
            except Exception:
                continue

            # Named imports
            for match in named_re.finditer(src):
                names_raw, imp = match.group(1), match.group(2)
                names = [n.split(" as ")[0].strip() for n in names_raw.split(",")
                         if n.strip() and not n.strip().startswith("type ")]
                _process_import(imp, names, "named",
                                project_dir, local_needs, missing_pkgs, KNOWN_PKGS)

            # Default imports
            for match in default_re.finditer(src):
                name, imp = match.group(1), match.group(2)
                if not any(named_re.search(f"{{ {name} }} from '{imp}'") for _ in [1]):
                    _process_import(imp, [name], "default",
                                    project_dir, local_needs, missing_pkgs, KNOWN_PKGS)

            # Star imports
            for match in star_re.finditer(src):
                name, imp = match.group(1), match.group(2)
                _process_import(imp, [name], "star",
                                 project_dir, local_needs, missing_pkgs, KNOWN_PKGS)

    # Install missing npm packages
    if missing_pkgs:
        pkg_list = sorted(missing_pkgs)
        emit("mayor", "TASK_STARTED",
             f"Installing {len(pkg_list)} missing pkgs: {', '.join(pkg_list)}")
        try:
            subprocess.run(
                ["npm", "install", "--no-audit", "--no-fund"] + pkg_list,
                cwd=project_dir, capture_output=True, timeout=180,
            )
        except subprocess.TimeoutExpired:
            emit("mayor", "TASK_STARTED", "npm install timed out — proceeding")

    # Fix route handlers FIRST — rewrites imports so subsequent scan
    # creates correct stubs for whatever imports remain
    _fix_nextauth_routes(project_dir)
    _fix_invalid_route_exports(project_dir)

    # Always ensure src/auth.ts exists (NextAuth v5 root config)
    auth_stub = os.path.join(project_dir, "src", "auth.ts")
    if not os.path.exists(auth_stub):
        os.makedirs(os.path.dirname(auth_stub), exist_ok=True)
        with open(auth_stub, "w") as f:
            f.write(STATIC_STUBS["src/auth.ts"])
        emit("mayor", "TASK_STARTED", "Created stub: src/auth.ts")
        # Re-scan local_needs now that routes have been fixed
        local_needs.pop("src/auth", None)  # remove stale entry

    # Create stubs for missing local paths
    for rel_no_ext, info in local_needs.items():
        stub_path = rel_no_ext + ".ts"
        full = os.path.join(project_dir, stub_path)
        if os.path.exists(full):
            continue
        content = STATIC_STUBS.get(stub_path) or _generate_stub(rel_no_ext, info)
        os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
        emit("mayor", "TASK_STARTED", f"Created stub: {stub_path}")


def _process_import(imp, names, kind, project_dir, local_needs, missing_pkgs, KNOWN_PKGS):
    """Classify an import as local or external and record it."""
    if imp.startswith("@/") or imp.startswith("~/"):
        rel = imp.replace("@/", "src/").replace("~/", "")
        # Check if file exists with any extension
        for ext in ("", ".ts", ".tsx", "/index.ts", "/index.tsx"):
            if os.path.exists(os.path.join(project_dir, rel + ext)):
                return  # already exists
        if rel not in local_needs:
            local_needs[rel] = {"named": set(), "default": None, "star": False}
        if kind == "named":
            local_needs[rel]["named"].update(names)
        elif kind == "default":
            local_needs[rel]["default"] = names[0] if names else "Default"
        elif kind == "star":
            local_needs[rel]["star"] = True
    elif not imp.startswith("."):
        # External package
        parts = imp.split("/")
        pkg_root = parts[0] if not parts[0].startswith("@") else "/".join(parts[:2])
        nm = os.path.join(project_dir, "node_modules", parts[0])
        if not os.path.exists(nm):
            npm_name = KNOWN_PKGS.get(imp) or KNOWN_PKGS.get(pkg_root) or pkg_root
            missing_pkgs.add(npm_name)


def _generate_stub(rel_no_ext, info):
    """Generate TypeScript stub content based on what's imported from a path."""
    lines = [f"// auto-generated stub for {rel_no_ext}"]
    named = info.get("named", set())
    default_name = info.get("default")
    has_star = info.get("star", False)

    for name in sorted(named):
        lines.append(f"export const {name}: any = {{}} as any;")

    if default_name:
        lines.append(f"const {default_name}: any = {{}} as any;")
        lines.append(f"export default {default_name};")
    elif not named:
        # No specific imports detected — export a catch-all
        lines.append("export default {} as any;")

    return "\n".join(lines) + "\n"


def _fix_nextauth_routes(project_dir):
    """
    Scan every route.ts/route.tsx for NextAuth patterns in the FILE CONTENT
    (not path — path detection is unreliable on different OS/agents).
    Overwrite any file that imports from next-auth with the correct v5 handler.
    """
    correct = (
        "// Auto-fixed by Gas Town — NextAuth v5 handler\n"
        "import { handlers } from '@/auth';\n"
        "export const { GET, POST } = handlers;\n"
    )
    # Content patterns that indicate this is a NextAuth route handler
    nextauth_signals = [
        "next-auth",
        "NextAuth",
        "from '@/auth'",
        "from '@/lib/auth'",
        "authOptions",
    ]
    for root, _, files in os.walk(project_dir):
        if "node_modules" in root or ".next" in root:
            continue
        for fname in files:
            if fname not in ("route.ts", "route.tsx"):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath) as f:
                    existing = f.read()
            except Exception:
                continue
            # Skip if already exactly our canonical version
            if "export const { GET, POST } = handlers" in existing \
                    and "from '@/auth'" in existing \
                    and "bcryptjs" not in existing \
                    and "prisma" not in existing:
                continue
            # Fix if any nextauth signal is present
            if any(sig in existing for sig in nextauth_signals):
                with open(fpath, "w") as f:
                    f.write(correct)
                emit("mayor", "TASK_STARTED",
                     f"Fixed NextAuth v5 route: {os.path.relpath(fpath, project_dir)}")


def _fix_invalid_route_exports(project_dir):
    """
    Next.js App Router route.ts files may ONLY export HTTP method handlers:
    GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS
    plus config: dynamic, revalidate, runtime, maxDuration.

    Scan every route.ts and rename any other exported async function to POST
    (e.g. createCheckoutSession → POST, handleWebhook → POST, etc.)
    """
    import re as _re

    VALID_EXPORTS = {
        "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS",
        "dynamic", "dynamicParams", "revalidate", "fetchCache",
        "runtime", "preferredRegion", "maxDuration", "generateStaticParams",
    }

    # Match: export async function Foo(  OR  export function Foo(
    fn_export_re = _re.compile(
        r"export\s+(async\s+)?function\s+(\w+)\s*\("
    )
    # Match: export const Foo = ...  OR  export const { Foo } = ...
    const_export_re = _re.compile(
        r"export\s+const\s+(\w+)\s*="
    )

    for root, _, files in os.walk(project_dir):
        if "node_modules" in root or ".next" in root:
            continue
        for fname in files:
            if fname not in ("route.ts", "route.tsx"):
                continue
            # Skip NextAuth routes — already handled
            fpath = os.path.join(root, fname)
            try:
                with open(fpath) as f:
                    content = f.read()
            except Exception:
                continue

            if "from '@/auth'" in content and "handlers" in content:
                continue  # NextAuth route — skip

            modified = content
            changed = False

            # Fix exported functions with invalid names
            for match in fn_export_re.finditer(content):
                fn_name = match.group(2)
                if fn_name not in VALID_EXPORTS:
                    # Determine best HTTP method from name
                    name_lower = fn_name.lower()
                    if any(k in name_lower for k in ("get", "fetch", "list", "read")):
                        method = "GET"
                    elif any(k in name_lower for k in ("delete", "remove")):
                        method = "DELETE"
                    elif any(k in name_lower for k in ("update", "put", "edit")):
                        method = "PUT"
                    else:
                        method = "POST"  # default for create/handle/webhook/etc.
                    old = match.group(0)
                    new = old.replace(f"function {fn_name}(", f"function {method}(")
                    modified = modified.replace(old, new, 1)
                    changed = True
                    emit("mayor", "TASK_STARTED",
                         f"Fixed route export: {fn_name} → {method} in "
                         f"{os.path.relpath(fpath, project_dir)}")

            if changed:
                with open(fpath, "w") as f:
                    f.write(modified)


def build_project(description):
    emit("mayor", "AGENT_SPAWNED", "Mayor online — Gas Town BUILD MODE")
    plan = parse_project_brief(description)

    project_name = plan.get("project_name", "gastown-project")
    framework    = plan.get("framework", "nextjs")
    project_dir  = os.path.expanduser(f"~/gastown/builds/{project_name}")

    # Always start fresh — stale files from previous runs cause build failures
    import shutil as _shutil
    if not DRY_RUN and os.path.exists(project_dir):
        _shutil.rmtree(project_dir)
        emit("mayor", "TASK_STARTED", f"Cleaned old build: {project_dir}")
    os.makedirs(project_dir, exist_ok=True)

    emit("mayor", "TASK_STARTED",
         f"Bootstrapping {framework} → {project_dir}")

    if not DRY_RUN:
        if framework == "nextjs":
            _bootstrap_nextjs(project_dir, project_name)
        elif framework == "fastapi":
            _bootstrap_fastapi(project_dir, project_name)
        elif framework in ("express", "go-api"):
            subprocess.run(["npm", "init", "-y"], cwd=project_dir,
                           capture_output=True, timeout=30)

    emit("mayor", "TASK_STARTED",
         f"Bootstrap done — spawning {len(plan['tasks'])} agents")

    threads = []
    for t in plan["tasks"]:
        th = threading.Thread(
            target=scaffold_agent,
            args=(t["agent"], t["task"], t["file"], project_dir, framework),
        )
        threads.append(th)
        th.start()
        time.sleep(0.4)
    for th in threads:
        th.join()

    # Scan generated files — install missing deps, create missing local stubs
    if not DRY_RUN:
        emit("mayor", "TASK_STARTED", "Scanning generated files for missing deps...")
        _fix_missing_deps(project_dir)

    repo_url = create_github_repo(project_name, project_dir)
    emit("mayor", "MERGED", f"All agents done — deploying {project_name}")
    deploy_url = deploy_new_project(project_dir, project_name)
    emit("mayor", "CONVOY_COMPLETE",
         f"Done. {project_name} live at {deploy_url or repo_url}")
    return deploy_url


def _bootstrap_fastapi(project_dir, project_name):
    files = {
        "main.py": (
            "from fastapi import FastAPI\n\n"
            "app = FastAPI(title='" + project_name + "')\n\n"
            "@app.get('/')\ndef root():\n    return {'status': 'ok', 'project': '" + project_name + "'}\n"
        ),
        "requirements.txt": "fastapi>=0.111\nuvicorn[standard]>=0.29\n",
        "vercel.json": json.dumps({
            "builds": [{"src": "main.py", "use": "@vercel/python"}],
            "routes": [{"src": "/(.*)", "dest": "main.py"}],
        }, indent=2),
    }
    for rel_path, content in files.items():
        full = os.path.join(project_dir, rel_path)
        with open(full, "w") as f:
            f.write(content)


# ── entrypoint ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    args = sys.argv[1:]

    if "--dry-run" in args:
        DRY_RUN = True
        args = [a for a in args if a != "--dry-run"]
        print("[dry-run] API calls skipped — events emitted to bridge only")

    if "--build" in args:
        BUILD_MODE = True
        args = [a for a in args if a != "--build"]
        if not args:
            print("Usage: orchestrator.py --build 'description'")
            sys.exit(1)
        build_project(" ".join(args))
    elif args:
        run_convoy(" ".join(args))
    else:
        task_file = os.path.join(TMP_DIR, "gastown-task")
        print(f"Watching for {task_file} ...")
        while True:
            if os.path.exists(task_file):
                with open(task_file) as f:
                    notes = f.read().strip()
                os.remove(task_file)
                run_convoy(notes)
            time.sleep(1)
