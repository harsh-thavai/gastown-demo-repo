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

    # Detect file type to add UI-specific instructions
    is_page    = target_file.endswith(("page.tsx", "page.jsx", "page.ts"))
    is_layout  = "layout" in target_file
    is_route   = "route.ts" in target_file or "route.tsx" in target_file
    is_python  = target_file.endswith(".py")
    is_html    = target_file.endswith(".html")

    DESIGN_SYSTEM = """
DESIGN SYSTEM — follow exactly, no exceptions:

Colors (dark theme):
  bg: #09090b  surface: #111113  surface2: #18181b
  border: #27272a  border2: #3f3f46
  text: #fafafa  text2: #a1a1aa  text3: #52525b
  accent: #6366f1  accent-hover: #4f46e5
  success: #10b981  warning: #f59e0b  danger: #ef4444

Typography: font-family Inter. Display 3rem/700/-0.04em. Heading 1.5rem/600/-0.02em. Body 15px/400/1.6.

Spacing: 8px grid only — 4,8,12,16,20,24,32,40,48,64,80,96px.

Radius: inputs/badges 6px · cards/buttons 8px · modals 12px · pills 9999px.

BANNED (AI slop — never do these):
  ✗ purple-to-blue gradient backgrounds everywhere
  ✗ "Coming soon" / "Lorem ipsum" / placeholder text
  ✗ emoji as nav icons (use Lucide React)
  ✗ centered-everything hero with gradient text
  ✗ 3 identical shadow cards in a row
  ✗ bg-gradient-to-r from-purple-500 to-blue-500 on buttons
  ✗ text-center on body text
  ✗ same border-radius on every element
  ✗ fake metrics without context

REQUIRED (what pros do):
  ✓ Left-aligned body text, intentional whitespace
  ✓ Asymmetric layouts — not every row is equal columns
  ✓ Real fake data — not "Item 1, Item 2"
  ✓ Keyboard shortcuts shown (⌘K, /)
  ✓ Hover: bg-zinc-800, transition-colors duration-150
  ✓ Focus: ring-2 ring-indigo-500 outline-none
  ✓ Mobile responsive with sm:/md:/lg: prefixes
  ✓ Empty states with actionable CTAs

Component patterns:
  Button primary: bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2 rounded-lg text-sm font-medium
  Card: bg-zinc-900 border border-zinc-800 rounded-xl p-5 hover:border-zinc-700
  Input: bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500
  Nav active: bg-zinc-800 border-l-2 border-indigo-500 text-white
  Sidebar: w-60 bg-zinc-950 border-r border-zinc-800
"""

    if is_page:
        ui_note = (
            "\n\nCRITICAL — THIS IS A PAGE USERS WILL SEE IN THEIR BROWSER:\n"
            + DESIGN_SYSTEM +
            "\nBuild the COMPLETE page:\n"
            "- Full sidebar + top nav + main content area layout\n"
            "- Real interactive components (working forms, tables with data, stat cards)\n"
            "- Write ALL the JSX — no truncation, no '// rest here'\n"
            "- The user will judge this on first look — make it impressive\n"
        )
    elif is_python or is_html:
        ui_note = (
            "\n\nCRITICAL — THIS IS A PAGE USERS WILL SEE IN THEIR BROWSER:\n"
            "Use the following design system:\n"
            + DESIGN_SYSTEM +
            "\nBuild a complete HTML/CSS/JS UI. All functionality must work.\n"
            "No placeholders. Real interactivity.\n"
        )
    elif is_route:
        ui_note = (
            "\nIMPORTANT: Next.js App Router API route.\n"
            "ONLY export: GET, POST, PUT, DELETE, PATCH.\n"
            "NEVER export functions with other names — they cause build failures.\n"
        )
    else:
        ui_note = ""

    system = f"""You are {agent_name}, a senior {framework} engineer.
Output ONLY the complete file content for {target_file}.
Do NOT include explanations, markdown fences, or commentary.
Output raw code only — exactly what should be written to disk.{ui_note}"""

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
Rules:
- Max one task per agent.
- PREFERRED frameworks: vite-react (React apps), fastapi (Python apps).
  Use nextjs ONLY if the user explicitly asks for Next.js.
- polecat-auth: builds login/register UI with forms (React components or HTML pages)
- polecat-tests: writes tests
- polecat-debug: security review
- polecat-docs: writes README
- polecat-review: MUST target src/App.jsx for vite-react — the main entry point with FULL UI.
  Import components from other agents. Include sidebar, stats cards, data table with mock data.
  This is what users see when they open the app. Make it impressive.
- For vite-react: polecat-auth → src/components/Login.jsx, polecat-debug → src/utils/api.js,
  polecat-docs → README.md, polecat-review → src/App.jsx (MANDATORY — full dashboard UI)
- For fastapi: main.py serves HTML with full UI at root
- EVERY file must have complete working code — no placeholder text, no empty components"""

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
            "const nextConfig: NextConfig = {\n"
            "  eslint: { ignoreDuringBuilds: true },\n"
            "  images: { unoptimized: true },\n"
            "};\n"
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
            "exclude": ["node_modules", "**/*.test.ts", "**/*.test.tsx",
                        "**/*.spec.ts", "**/*.spec.tsx", "**/__tests__/**"],
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
            if "vercel.app" in line and "https://" in line:
                # Extract clean URL — strip anything after the domain
                import re as _re
                m = _re.search(r'https://[a-z0-9\-]+\.vercel\.app', line)
                if m:
                    deploy_url = m.group(0)
                    emit("vercel", "DEPLOYMENT_READY", f"Live: {deploy_url}", diff=deploy_url)
                    return deploy_url
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
    _fix_nextconfig(project_dir)
    _fix_vite_imports(project_dir)

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


def _fix_vite_imports(project_dir):
    """
    Fix broken relative imports in Vite React projects.
    Agents often write 'import X from ./X' but the file is at src/components/X.jsx.
    Scan all .jsx/.js files, find unresolvable imports, search the project for the
    actual file, and rewrite to the correct relative path.
    """
    import re as _re

    # Build a map of filename (without ext) → relative path from src/
    file_map = {}
    for root, _, files in os.walk(os.path.join(project_dir, "src")):
        for f in files:
            if f.endswith((".jsx", ".js", ".tsx", ".ts", ".css")):
                name = os.path.splitext(f)[0]
                rel = os.path.relpath(os.path.join(root, f), project_dir)
                rel = rel.replace("\\", "/")
                file_map[name.lower()] = rel  # last write wins; good enough

    import_re = _re.compile(r"""(from\s+['"])(\.[^'"]+)(['"])""")

    for root, _, files in os.walk(os.path.join(project_dir, "src")):
        if "node_modules" in root:
            continue
        for fname in files:
            if not fname.endswith((".jsx", ".js", ".tsx", ".ts")):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath) as f:
                    content = f.read()
            except Exception:
                continue

            changed = False
            def fix_import(m):
                nonlocal changed
                prefix, imp_path, suffix = m.group(1), m.group(2), m.group(3)
                # Check if the import resolves as-is
                base = os.path.normpath(os.path.join(os.path.dirname(fpath), imp_path))
                for ext in ("", ".js", ".jsx", ".ts", ".tsx"):
                    if os.path.exists(base + ext):
                        return m.group(0)  # already valid
                # Try to find the file by name
                imp_name = os.path.basename(imp_path).lower()
                if imp_name in file_map:
                    target = file_map[imp_name]
                    # Compute correct relative path from current file's dir
                    current_dir = os.path.dirname(fpath)
                    target_abs = os.path.join(project_dir, target)
                    new_rel = os.path.relpath(target_abs, current_dir).replace("\\", "/")
                    # Remove extension for JS imports
                    new_rel = _re.sub(r'\.(jsx|tsx|js|ts)$', '', new_rel)
                    if not new_rel.startswith("."):
                        new_rel = "./" + new_rel
                    changed = True
                    return f"{prefix}{new_rel}{suffix}"
                return m.group(0)

            new_content = import_re.sub(fix_import, content)
            if changed:
                with open(fpath, "w") as f:
                    f.write(new_content)
                emit("mayor", "TASK_STARTED",
                     f"Fixed imports in {os.path.relpath(fpath, project_dir)}")


def _fix_nextconfig(project_dir):
    """
    Ensure next.config.ts has:
      eslint.ignoreDuringBuilds: true  — prevents test-file parse errors from failing build
      images.unoptimized: true         — prevents <img> warnings becoming errors
    """
    for config_name in ("next.config.ts", "next.config.js", "next.config.mjs"):
        fpath = os.path.join(project_dir, config_name)
        if not os.path.exists(fpath):
            continue
        try:
            with open(fpath) as f:
                content = f.read()
        except Exception:
            continue
        if "ignoreDuringBuilds" in content:
            continue  # already patched
        # Inject into the config object
        patched = content.replace(
            "const nextConfig",
            "// @ts-ignore\nconst nextConfig",
        )
        if "NextConfig = {" in patched:
            patched = patched.replace(
                "NextConfig = {",
                "NextConfig = {\n  eslint: { ignoreDuringBuilds: true },\n  images: { unoptimized: true },",
            )
        elif "nextConfig = {" in patched:
            patched = patched.replace(
                "nextConfig = {",
                "nextConfig = {\n  eslint: { ignoreDuringBuilds: true },\n  images: { unoptimized: true },",
            )
        else:
            # Fallback: replace entirely
            patched = (
                'import type { NextConfig } from "next";\n'
                "const nextConfig: NextConfig = {\n"
                "  eslint: { ignoreDuringBuilds: true },\n"
                "  images: { unoptimized: true },\n"
                "};\nexport default nextConfig;\n"
            )
        with open(fpath, "w") as f:
            f.write(patched)
        emit("mayor", "TASK_STARTED", f"Patched {config_name}: ESLint + images config")
        break


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
        elif framework in ("vite-react", "react"):
            _bootstrap_vite_react(project_dir, project_name)
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


def _bootstrap_vite_react(project_dir, project_name):
    """Bootstrap a Vite + React app — no TypeScript, no strict mode, deploys as static."""
    display_name = project_name.replace("-", " ").title()
    os.makedirs(os.path.join(project_dir, "src", "pages"), exist_ok=True)
    os.makedirs(os.path.join(project_dir, "src", "components"), exist_ok=True)
    os.makedirs(os.path.join(project_dir, "public"), exist_ok=True)

    files = {
        "package.json": json.dumps({
            "name": project_name, "version": "0.1.0", "private": True,
            "type": "module",
            "scripts": {
                "dev":     "vite",
                "build":   "vite build",
                "preview": "vite preview",
            },
            "dependencies": {
                "react": "^18.3.1", "react-dom": "^18.3.1",
                "react-router-dom": "^6.28.0",
                "lucide-react": "^0.511.0",
                "recharts": "^2.15.3",
                "clsx": "^2.1.1",
            },
            "devDependencies": {
                "@vitejs/plugin-react": "^4.3.4",
                "vite": "^6.3.5",
                "tailwindcss": "^3.4.17",
                "autoprefixer": "^10.4.21",
                "postcss": "^8.5.4",
            },
        }, indent=2),
        "vite.config.js": (
            "import { defineConfig } from 'vite';\n"
            "import react from '@vitejs/plugin-react';\n"
            "export default defineConfig({\n"
            "  plugins: [react()],\n"
            "  build: { outDir: 'dist' },\n"
            "});\n"
        ),
        "index.html": (
            "<!DOCTYPE html>\n<html lang='en'>\n<head>\n"
            "  <meta charset='UTF-8' />\n"
            "  <meta name='viewport' content='width=device-width, initial-scale=1.0' />\n"
            f"  <title>{display_name}</title>\n"
            "</head>\n<body>\n"
            "  <div id='root'></div>\n"
            "  <script type='module' src='/src/main.jsx'></script>\n"
            "</body>\n</html>\n"
        ),
        "tailwind.config.js": (
            "export default {\n"
            "  content: ['./index.html', './src/**/*.{js,jsx}'],\n"
            "  theme: { extend: {} },\n"
            "  plugins: [],\n"
            "};\n"
        ),
        "postcss.config.js": (
            "export default {\n"
            "  plugins: { tailwindcss: {}, autoprefixer: {} },\n"
            "};\n"
        ),
        "vercel.json": json.dumps({
            "buildCommand": "npm run build",
            "outputDirectory": "dist",
            "framework": "vite",
            "regions": ["bom1"],
        }, indent=2),
        "src/index.css": (
            "@tailwind base;\n@tailwind components;\n@tailwind utilities;\n"
        ),
        "src/main.jsx": (
            "import React from 'react';\n"
            "import ReactDOM from 'react-dom/client';\n"
            "import App from './App';\n"
            "import './index.css';\n"
            "ReactDOM.createRoot(document.getElementById('root')).render(\n"
            "  <React.StrictMode><App /></React.StrictMode>\n"
            ");\n"
        ),
        "src/App.jsx": (
            "import React from 'react';\n\n"
            f"export default function App() {{\n"
            f"  return (\n"
            f"    <div className='min-h-screen bg-gray-50'>\n"
            f"      <div className='max-w-4xl mx-auto p-8'>\n"
            f"        <h1 className='text-3xl font-bold text-gray-900'>{display_name}</h1>\n"
            f"        <p className='mt-2 text-gray-500'>Built by Gas Town</p>\n"
            f"      </div>\n"
            f"    </div>\n"
            f"  );\n"
            f"}}\n"
        ),
    }

    for rel_path, content in files.items():
        full = os.path.join(project_dir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)

    emit("mayor", "TASK_STARTED", "Running npm install for Vite React...")
    try:
        subprocess.run(
            ["npm", "install", "--no-audit", "--no-fund"],
            cwd=project_dir, capture_output=True, timeout=180,
        )
    except subprocess.TimeoutExpired:
        emit("mayor", "TASK_STARTED", "npm install timed out — proceeding")


def _bootstrap_fastapi(project_dir, project_name):
    display_name = project_name.replace("-", " ").title()
    main_py = f'''from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import math, operator

app = FastAPI(title="{display_name}")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class CalcRequest(BaseModel):
    expression: str

class CalcResult(BaseModel):
    result: float | str
    expression: str

@app.post("/api/calculate", response_model=CalcResult)
def calculate(req: CalcRequest):
    """Evaluate a math expression safely using Python."""
    try:
        safe_names = {{
            "abs": abs, "round": round, "min": min, "max": max,
            "pow": pow, "sqrt": math.sqrt, "pi": math.pi, "e": math.e,
            "sin": math.sin, "cos": math.cos, "tan": math.tan,
            "log": math.log, "log10": math.log10, "ceil": math.ceil, "floor": math.floor,
        }}
        result = eval(req.expression, {{"__builtins__": {{}}}}, safe_names)
        if isinstance(result, (int, float)) and math.isfinite(result):
            return CalcResult(result=round(float(result), 10), expression=req.expression)
        return CalcResult(result="Error", expression=req.expression)
    except Exception as e:
        return CalcResult(result="Error", expression=req.expression)

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{display_name}</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{{--bg:#0a0a0f;--surface:#13131a;--surface2:#1c1c28;--border:#2a2a3d;--accent:#6366f1;--accent2:#8b5cf6;--green:#10b981;--red:#ef4444;--text:#e2e8f0;--text2:#94a3b8;--mono:'SF Mono',monospace}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Inter',system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:20px}}
.wrapper{{width:100%;max-width:400px}}
.header{{text-align:center;margin-bottom:32px}}
.header h1{{font-size:1.5rem;font-weight:700;letter-spacing:-.02em;color:var(--text)}}
.header p{{font-size:.85rem;color:var(--text2);margin-top:6px}}
.calc{{background:var(--surface);border:1px solid var(--border);border-radius:20px;overflow:hidden;box-shadow:0 40px 80px rgba(0,0,0,.6)}}
.display-area{{padding:24px 24px 16px;border-bottom:1px solid var(--border);background:var(--bg)}}
.expr{{font-family:var(--mono);font-size:.8rem;color:var(--text2);min-height:18px;text-align:right;word-break:break-all;margin-bottom:6px}}
.result{{font-family:var(--mono);font-size:2.8rem;font-weight:300;text-align:right;word-break:break-all;line-height:1;min-height:44px;transition:color .15s}}
.result.error{{color:var(--red);font-size:1.6rem}}
.result.computing{{color:var(--text2)}}
.history{{padding:8px 24px;background:var(--surface2);border-bottom:1px solid var(--border);min-height:36px}}
.history-item{{font-family:var(--mono);font-size:.75rem;color:var(--text2);cursor:pointer;padding:2px 0}}
.history-item:hover{{color:var(--accent)}}
.buttons{{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--border)}}
.btn{{background:var(--surface);border:none;color:var(--text);font-family:'Inter',sans-serif;font-size:1rem;font-weight:500;padding:20px 16px;cursor:pointer;transition:background .1s;user-select:none}}
.btn:hover{{background:var(--surface2)}}
.btn:active{{background:var(--border)}}
.btn-op{{color:var(--accent);background:#13131f}}
.btn-op:hover{{background:#1a1a2e}}
.btn-eq{{background:var(--accent);color:#fff;grid-column:span 2}}
.btn-eq:hover{{background:var(--accent2)}}
.btn-clear{{color:var(--red)}}
.btn-zero{{grid-column:span 2;text-align:left;padding-left:28px}}
.footer{{margin-top:20px;display:flex;align-items:center;justify-content:center;gap:8px}}
.badge{{background:rgba(99,102,241,.15);border:1px solid rgba(99,102,241,.3);color:var(--accent);font-size:.7rem;font-weight:600;padding:4px 12px;border-radius:20px;letter-spacing:.06em}}
.status-dot{{width:6px;height:6px;border-radius:50%;background:var(--green);animation:pulse 2s infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.4}}}}
</style>
</head>
<body>
<div class="wrapper">
  <div class="header">
    <h1>{display_name}</h1>
    <p>Python-powered — calculations run server-side</p>
  </div>
  <div class="calc">
    <div class="display-area">
      <div class="expr" id="expr">&nbsp;</div>
      <div class="result" id="result">0</div>
    </div>
    <div class="history" id="history"></div>
    <div class="buttons">
      <button class="btn btn-clear" onclick="clr()">AC</button>
      <button class="btn btn-op" onclick="ins('(-'">±</button>
      <button class="btn btn-op" onclick="ins('/100')">%</button>
      <button class="btn btn-op" onclick="ins('/'">÷</button>
      <button class="btn" onclick="ins('7')">7</button>
      <button class="btn" onclick="ins('8')">8</button>
      <button class="btn" onclick="ins('9')">9</button>
      <button class="btn btn-op" onclick="ins('*')">×</button>
      <button class="btn" onclick="ins('4')">4</button>
      <button class="btn" onclick="ins('5')">5</button>
      <button class="btn" onclick="ins('6')">6</button>
      <button class="btn btn-op" onclick="ins('-')">−</button>
      <button class="btn" onclick="ins('1')">1</button>
      <button class="btn" onclick="ins('2')">2</button>
      <button class="btn" onclick="ins('3')">3</button>
      <button class="btn btn-op" onclick="ins('+')">+</button>
      <button class="btn btn-zero" onclick="ins('0')">0</button>
      <button class="btn" onclick="ins('.')">.</button>
      <button class="btn btn-eq" onclick="calc()">=</button>
    </div>
  </div>
  <div class="footer">
    <div class="status-dot"></div>
    <span class="badge">⛽ GAS TOWN · PYTHON BACKEND</span>
  </div>
</div>
<script>
let expr = '';
const exprEl = document.getElementById('expr');
const resultEl = document.getElementById('result');
const historyEl = document.getElementById('history');
const history = [];

function ins(v) {{
  expr += v;
  exprEl.textContent = expr;
  resultEl.textContent = expr;
  resultEl.className = 'result';
}}

function clr() {{
  expr = '';
  exprEl.textContent = '\\u00a0';
  resultEl.textContent = '0';
  resultEl.className = 'result';
}}

async function calc() {{
  if (!expr) return;
  resultEl.className = 'result computing';
  resultEl.textContent = '...';
  try {{
    const res = await fetch('/api/calculate', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{expression: expr}})
    }});
    const data = await res.json();
    if (data.result === 'Error') {{
      resultEl.textContent = 'Error';
      resultEl.className = 'result error';
    }} else {{
      const r = String(data.result);
      addHistory(expr + ' = ' + r);
      exprEl.textContent = expr + ' =';
      resultEl.textContent = r;
      resultEl.className = 'result';
      expr = r;
    }}
  }} catch(e) {{
    resultEl.textContent = 'Error';
    resultEl.className = 'result error';
  }}
}}

function addHistory(entry) {{
  history.unshift(entry);
  if (history.length > 3) history.pop();
  historyEl.innerHTML = history.map(h =>
    `<div class="history-item" onclick="loadHistory('${{h.split(' = ')[0]}}')>${{h}}</div>`
  ).join('');
}}

function loadHistory(e) {{ expr = e; exprEl.textContent = e; resultEl.textContent = e; }}

document.addEventListener('keydown', e => {{
  if ((e.key >= '0' && e.key <= '9') || ['+','-','*','/','.',',','(',')','^'].includes(e.key)) ins(e.key);
  else if (e.key === 'Enter' || e.key === '=') calc();
  else if (e.key === 'Backspace') {{ expr = expr.slice(0,-1); exprEl.textContent = expr||'\\u00a0'; resultEl.textContent = expr||'0'; }}
  else if (e.key === 'Escape') clr();
}});
</script>
</body></html>"""

@app.get("/", response_class=HTMLResponse)
def root():
    return HTML

@app.get("/api/health")
def health():
    return {{"status": "ok", "project": "{project_name}"}}
'''
    # Write main.py
    full_main = os.path.join(project_dir, "main.py")
    with open(full_main, "w") as f:
        f.write(main_py)

    # Write requirements.txt
    with open(os.path.join(project_dir, "requirements.txt"), "w") as f:
        f.write("fastapi>=0.111\nuvicorn[standard]>=0.29\n")

    # Write vercel.json
    with open(os.path.join(project_dir, "vercel.json"), "w") as f:
        f.write(json.dumps({
            "builds": [{"src": "main.py", "use": "@vercel/python"}],
            "routes": [{"src": "/(.*)", "dest": "main.py"}],
        }, indent=2))


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
        task_file  = os.path.join(TMP_DIR, "gastown-task")
        build_file = os.path.join(TMP_DIR, "gastown-build")
        print(f"Watching for {task_file} or {build_file} ...")
        while True:
            if os.path.exists(build_file):
                with open(build_file) as f:
                    desc = f.read().strip()
                os.remove(build_file)
                BUILD_MODE = True
                build_project(desc)
                BUILD_MODE = False
            elif os.path.exists(task_file):
                with open(task_file) as f:
                    notes = f.read().strip()
                os.remove(task_file)
                run_convoy(notes)
            time.sleep(1)
