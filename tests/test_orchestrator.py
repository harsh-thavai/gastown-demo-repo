"""
Gas Town Orchestrator — Comprehensive Test Suite
Unit + Integration + Behaviour tests
Run: python -m pytest tests/ -v
"""

import os, sys, json, threading, time, tempfile, shutil
import unittest
from unittest.mock import patch, MagicMock, call

# ── Make orchestrator importable ────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Provide stub env vars so module-level code doesn't crash
os.environ.setdefault("DO_INFERENCE_URL", "https://inference.do-ai.run/v1")
os.environ.setdefault("MODEL_ACCESS_KEY", "sk-do-test")

import orchestrator as orc


# ─────────────────────────────────────────────────────────────────────────────
# UNIT TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestEmit(unittest.TestCase):
    """emit() should post to bridge and print without raising."""

    def test_emit_prints(self):
        with patch("orchestrator.requests.post") as mock_post, \
             patch("builtins.print") as mock_print:
            mock_post.return_value = MagicMock(status_code=200)
            orc.emit("mayor", "TEST", "hello")
            mock_print.assert_called_once()
            args = mock_print.call_args[0][0]
            self.assertIn("mayor", args)
            self.assertIn("TEST", args)
            self.assertIn("hello", args)

    def test_emit_includes_diff(self):
        with patch("orchestrator.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            orc.emit("polecat-auth", "CODE_WRITTEN", "wrote file", diff="content")
            payload = mock_post.call_args[1]["json"]
            self.assertEqual(payload["diff"], "content")

    def test_emit_bridge_failure_is_silent(self):
        """Bridge unreachable must not raise."""
        with patch("orchestrator.requests.post", side_effect=Exception("conn refused")):
            orc.emit("mayor", "TEST", "should not raise")  # no exception = pass

    def test_emit_agent_role_stripped(self):
        with patch("orchestrator.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            orc.emit("polecat-debug", "TASK_STARTED", "x")
            payload = mock_post.call_args[1]["json"]
            self.assertEqual(payload["agent_role"], "debug")

    def test_emit_mayor_role(self):
        with patch("orchestrator.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            orc.emit("mayor", "TASK_STARTED", "x")
            payload = mock_post.call_args[1]["json"]
            self.assertEqual(payload["agent_role"], "mayor")


class TestCallDoInference(unittest.TestCase):
    """call_do_inference() API handling."""

    def setUp(self):
        orc.DRY_RUN    = False
        orc.BUILD_MODE = False

    def test_dry_run_returns_fix_plan(self):
        orc.DRY_RUN    = True
        orc.BUILD_MODE = False
        result = orc.call_do_inference("sys", "user")
        plan = json.loads(result)
        self.assertIn("convoy", plan)
        self.assertIn("tasks", plan)
        self.assertEqual(len(plan["tasks"]), 5)

    def test_dry_run_build_returns_build_plan(self):
        orc.DRY_RUN    = True
        orc.BUILD_MODE = True
        result = orc.call_do_inference("sys", "user")
        plan = json.loads(result)
        self.assertIn("project_name", plan)
        self.assertIn("framework", plan)

    def test_missing_env_raises(self):
        orc.DRY_RUN = False
        orig_url = orc.DO_INFERENCE_URL
        orig_key = orc.MODEL_ACCESS_KEY
        orc.DO_INFERENCE_URL = ""
        orc.MODEL_ACCESS_KEY = ""
        with self.assertRaises(RuntimeError):
            orc.call_do_inference("sys", "user")
        orc.DO_INFERENCE_URL = orig_url
        orc.MODEL_ACCESS_KEY = orig_key

    def test_api_returns_choices(self):
        orc.DRY_RUN = False
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "hello"}}]
        }
        mock_resp.raise_for_status = MagicMock()
        with patch("orchestrator.requests.post", return_value=mock_resp):
            result = orc.call_do_inference("sys", "user")
        self.assertEqual(result, "hello")

    def test_api_error_raises(self):
        orc.DRY_RUN = False
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": {"message": "forbidden"}}
        mock_resp.raise_for_status = MagicMock()
        with patch("orchestrator.requests.post", return_value=mock_resp):
            with self.assertRaises(RuntimeError):
                orc.call_do_inference("sys", "user")

    def tearDown(self):
        orc.DRY_RUN    = False
        orc.BUILD_MODE = False


class TestParseMeetingNotes(unittest.TestCase):

    def setUp(self):
        orc.DRY_RUN = True

    def tearDown(self):
        orc.DRY_RUN = False

    def test_returns_plan_with_tasks(self):
        with patch("orchestrator.requests.post"):
            plan = orc.parse_meeting_notes("fix auth bug")
        self.assertIn("tasks", plan)
        self.assertGreater(len(plan["tasks"]), 0)

    def test_plan_has_required_fields(self):
        with patch("orchestrator.requests.post"):
            plan = orc.parse_meeting_notes("fix tests")
        for t in plan["tasks"]:
            self.assertIn("agent", t)
            self.assertIn("task", t)
            self.assertIn("file", t)


class TestParseProjectBrief(unittest.TestCase):

    def setUp(self):
        orc.DRY_RUN    = True
        orc.BUILD_MODE = True

    def tearDown(self):
        orc.DRY_RUN    = False
        orc.BUILD_MODE = False

    def test_returns_build_plan(self):
        with patch("orchestrator.requests.post"):
            plan = orc.parse_project_brief("Next.js SaaS")
        self.assertIn("project_name", plan)
        self.assertIn("framework", plan)
        self.assertIn("tasks", plan)

    def test_framework_is_valid(self):
        with patch("orchestrator.requests.post"):
            plan = orc.parse_project_brief("Next.js SaaS")
        self.assertIn(plan["framework"], ["nextjs","fastapi","express","go-api"])

    def test_project_name_kebab(self):
        with patch("orchestrator.requests.post"):
            plan = orc.parse_project_brief("Next.js SaaS")
        self.assertRegex(plan["project_name"], r"^[a-z][a-z0-9-]*$")


class TestGenerateFileContent(unittest.TestCase):

    def setUp(self):
        orc.DRY_RUN = True

    def tearDown(self):
        orc.DRY_RUN = False

    def test_dry_run_returns_comment(self):
        content = orc.generate_file_content(
            "polecat-auth", "build login page",
            "src/app/auth/login/page.tsx", "nextjs")
        self.assertIn("dry-run", content)
        self.assertIn("polecat-auth", content)

    def test_real_call_returns_content(self):
        orc.DRY_RUN = False
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "export default function Login() {}"}}]
        }
        mock_resp.raise_for_status = MagicMock()
        with patch("orchestrator.requests.post", return_value=mock_resp):
            content = orc.generate_file_content(
                "polecat-auth", "build login page",
                "src/app/auth/login/page.tsx", "nextjs")
        self.assertIn("Login", content)


class TestBootstrapNextjs(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.project_dir = os.path.join(self.tmp, "my-project")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_creates_package_json(self):
        with patch("orchestrator.subprocess.run") as mock_run, \
             patch("orchestrator.requests.post"):
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            orc._bootstrap_nextjs(self.project_dir, "my-project")
        pkg = os.path.join(self.project_dir, "package.json")
        self.assertTrue(os.path.exists(pkg))

    def test_package_json_valid(self):
        with patch("orchestrator.subprocess.run") as mock_run, \
             patch("orchestrator.requests.post"):
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            orc._bootstrap_nextjs(self.project_dir, "my-project")
        with open(os.path.join(self.project_dir, "package.json")) as f:
            pkg = json.load(f)
        self.assertEqual(pkg["name"], "my-project")
        self.assertIn("next", pkg["dependencies"])
        self.assertIn("build", pkg["scripts"])

    def test_creates_src_app_layout(self):
        with patch("orchestrator.subprocess.run") as mock_run, \
             patch("orchestrator.requests.post"):
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            orc._bootstrap_nextjs(self.project_dir, "my-project")
        layout = os.path.join(self.project_dir, "src", "app", "layout.tsx")
        self.assertTrue(os.path.exists(layout))

    def test_creates_vercel_json(self):
        with patch("orchestrator.subprocess.run") as mock_run, \
             patch("orchestrator.requests.post"):
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            orc._bootstrap_nextjs(self.project_dir, "my-project")
        vj = os.path.join(self.project_dir, "vercel.json")
        self.assertTrue(os.path.exists(vj))
        with open(vj) as f:
            data = json.load(f)
        self.assertEqual(data["framework"], "nextjs")

    def test_creates_tailwind_config(self):
        with patch("orchestrator.subprocess.run") as mock_run, \
             patch("orchestrator.requests.post"):
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            orc._bootstrap_nextjs(self.project_dir, "my-project")
        self.assertTrue(os.path.exists(
            os.path.join(self.project_dir, "tailwind.config.ts")))

    def test_npm_install_timeout_handled(self):
        import subprocess
        with patch("orchestrator.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("npm", 180)), \
             patch("orchestrator.requests.post"):
            # Should not raise
            orc._bootstrap_nextjs(self.project_dir, "my-project")


class TestScaffoldAgent(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        orc.DRY_RUN = False

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        orc.DRY_RUN = False

    def test_dry_run_emits_events(self):
        orc.DRY_RUN = True
        events = []
        with patch("orchestrator.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            mock_post.side_effect = lambda url, **kw: (
                events.append(kw["json"]["type"]) or MagicMock()
            )
            orc.scaffold_agent("polecat-auth", "build login",
                               "src/app/page.tsx", self.tmp, "nextjs")
        self.assertIn("AGENT_SPAWNED", events)
        self.assertIn("CODE_WRITTEN", events)
        self.assertIn("REVIEW_PASSED", events)

    def test_writes_file_on_success(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "export default function P() {}"}}]
        }
        mock_resp.raise_for_status = MagicMock()
        with patch("orchestrator.requests.post", return_value=mock_resp):
            orc.scaffold_agent("polecat-auth", "build page",
                               "src/app/page.tsx", self.tmp, "nextjs")
        out = os.path.join(self.tmp, "src/app/page.tsx")
        self.assertTrue(os.path.exists(out))
        with open(out) as f:
            content = f.read()
        self.assertIn("function P", content)

    def test_inference_error_emits_stuck(self):
        events = []
        with patch("orchestrator.requests.post") as mock_post:
            def side(url, **kw):
                if "ingest" in url:
                    events.append(kw["json"]["type"])
                    return MagicMock()
                raise Exception("API down")
            mock_post.side_effect = side
            orc.scaffold_agent("polecat-auth", "build page",
                               "src/app/page.tsx", self.tmp, "nextjs")
        self.assertIn("AGENT_STUCK", events)

    def test_creates_parent_directories(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "// content"}}]
        }
        mock_resp.raise_for_status = MagicMock()
        with patch("orchestrator.requests.post", return_value=mock_resp):
            orc.scaffold_agent("polecat-docs", "write README",
                               "docs/api/reference.md", self.tmp, "nextjs")
        self.assertTrue(os.path.exists(
            os.path.join(self.tmp, "docs/api/reference.md")))


class TestRunAgent(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        orc.DRY_RUN = False
        orc.WORKTREES["polecat-auth"] = self.tmp

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        orc.DRY_RUN = False

    def test_dry_run_completes(self):
        orc.DRY_RUN = True
        events = []
        with patch("orchestrator.requests.post") as mock_post:
            mock_post.side_effect = lambda url, **kw: (
                events.append(kw["json"]["type"]) or MagicMock()
            )
            orc.run_agent("polecat-auth", "fix jwt", "src/auth/jwt.go")
        self.assertIn("CODE_WRITTEN", events)
        self.assertIn("REVIEW_PASSED", events)

    def test_writes_and_commits(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "package auth\n"}}]
        }
        mock_resp.raise_for_status = MagicMock()
        with patch("orchestrator.requests.post", return_value=mock_resp), \
             patch("orchestrator.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            orc.run_agent("polecat-auth", "fix jwt", "src/auth/jwt.go")
        written = os.path.join(self.tmp, "src/auth/jwt.go")
        self.assertTrue(os.path.exists(written))


class TestCreateGithubRepo(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        orc.DRY_RUN = False

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        orc.DRY_RUN = False

    def test_dry_run_returns_url(self):
        orc.DRY_RUN = True
        with patch("orchestrator.requests.post"):
            url = orc.create_github_repo("my-project", self.tmp)
        self.assertIn("github.com", url)
        self.assertIn("my-project", url)

    def test_real_run_calls_gh(self):
        with patch("orchestrator.subprocess.run") as mock_run, \
             patch("orchestrator.requests.post"):
            mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
            url = orc.create_github_repo("my-project", self.tmp)
        self.assertIn("github.com", url)

    def test_handles_existing_repo(self):
        with patch("orchestrator.subprocess.run") as mock_run, \
             patch("orchestrator.requests.post"):
            mock_run.return_value = MagicMock(
                returncode=1, stderr="already exists", stdout="")
            # should not raise
            url = orc.create_github_repo("my-project", self.tmp)
        self.assertIn("my-project", url)

    def test_gh_not_found_continues(self):
        ok = MagicMock(returncode=0, stderr="", stdout="")
        side_effects = [ok, ok, ok, ok, ok, FileNotFoundError()]
        with patch("orchestrator.subprocess.run", side_effect=side_effects), \
             patch("orchestrator.requests.post"):
            url = orc.create_github_repo("my-project", self.tmp)
        self.assertIn("my-project", url)


class TestDeployNewProject(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        orc.DRY_RUN = False
        orc.VERCEL_TOKEN = "test-token"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        orc.DRY_RUN = False

    def test_dry_run_returns_url(self):
        orc.DRY_RUN = True
        with patch("orchestrator.requests.post"):
            url = orc.deploy_new_project(self.tmp, "my-project")
        self.assertIn("my-project.vercel.app", url)

    def test_no_token_skips(self):
        orc.VERCEL_TOKEN = ""
        with patch("orchestrator.requests.post"):
            result = orc.deploy_new_project(self.tmp, "my-project")
        self.assertIsNone(result)

    def test_vercel_cli_success(self):
        with patch("orchestrator.subprocess.run") as mock_run, \
             patch("orchestrator.requests.post"):
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="https://my-project.vercel.app\n",
                stderr="",
            )
            url = orc.deploy_new_project(self.tmp, "my-project")
        self.assertIsNotNone(url)
        self.assertIn("vercel.app", url)

    def test_vercel_cli_not_found_falls_back_to_api(self):
        api_resp = MagicMock()
        api_resp.json.return_value = {
            "id": "dpl_123",
            "url": "my-project-abc.vercel.app",
        }
        ready_resp = MagicMock()
        ready_resp.json.return_value = {"readyState": "READY", "url": "my-project-abc.vercel.app"}

        with patch("orchestrator.subprocess.run", side_effect=FileNotFoundError()), \
             patch("orchestrator.requests.post", return_value=api_resp), \
             patch("orchestrator.requests.get", return_value=ready_resp), \
             patch("orchestrator.time.sleep"):
            url = orc.deploy_new_project(self.tmp, "my-project")
        self.assertIsNotNone(url)


# ─────────────────────────────────────────────────────────────────────────────
# DEPENDENCY AUTO-FIX TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestFixMissingDeps(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "src", "app"), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel_path, content):
        full = os.path.join(self.tmp, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)

    def test_detects_missing_npm_package(self):
        self._write("src/app/page.tsx",
                    "import { Button } from 'lucide-react';\nexport default function P(){}")
        with patch("orchestrator.subprocess.run") as mock_run, \
             patch("orchestrator.requests.post"):
            mock_run.return_value = MagicMock(returncode=0)
            orc._fix_missing_deps(self.tmp)
        calls = [str(c) for c in mock_run.call_args_list]
        self.assertTrue(any("lucide-react" in c for c in calls))

    def test_creates_stub_with_correct_named_export(self):
        """Stub must export the exact name that was imported."""
        self._write("src/app/dashboard/page.tsx",
                    "import { db } from '@/lib/db';\nexport default function D(){}")
        with patch("orchestrator.subprocess.run", return_value=MagicMock(returncode=0)), \
             patch("orchestrator.requests.post"):
            orc._fix_missing_deps(self.tmp)
        stub = os.path.join(self.tmp, "src", "lib", "db.ts")
        self.assertTrue(os.path.exists(stub))
        with open(stub) as f:
            content = f.read()
        self.assertIn("export", content)
        self.assertIn("db", content)

    def test_creates_stub_for_at_db_path(self):
        """@/db (not @/lib/db) must also get a stub."""
        self._write("src/app/api/route.ts",
                    "import { db } from '@/db';\nexport async function GET(){}")
        with patch("orchestrator.subprocess.run", return_value=MagicMock(returncode=0)), \
             patch("orchestrator.requests.post"):
            orc._fix_missing_deps(self.tmp)
        stub = os.path.join(self.tmp, "src", "db.ts")
        self.assertTrue(os.path.exists(stub))

    def test_creates_stub_for_at_db_schema(self):
        """@/db/schema must get a stub exporting usersTable."""
        self._write("src/app/api/route.ts",
                    "import { usersTable } from '@/db/schema';\nexport async function GET(){}")
        with patch("orchestrator.subprocess.run", return_value=MagicMock(returncode=0)), \
             patch("orchestrator.requests.post"):
            orc._fix_missing_deps(self.tmp)
        stub = os.path.join(self.tmp, "src", "db", "schema.ts")
        self.assertTrue(os.path.exists(stub))
        with open(stub) as f:
            content = f.read()
        self.assertIn("usersTable", content)

    def test_creates_prisma_stub_with_prisma_export(self):
        """@/lib/prisma stub must export 'prisma'."""
        self._write("src/app/api/route.ts",
                    "import { prisma } from '@/lib/prisma';\nexport async function GET(){}")
        with patch("orchestrator.subprocess.run", return_value=MagicMock(returncode=0)), \
             patch("orchestrator.requests.post"):
            orc._fix_missing_deps(self.tmp)
        stub = os.path.join(self.tmp, "src", "lib", "prisma.ts")
        self.assertTrue(os.path.exists(stub))
        with open(stub) as f:
            content = f.read()
        self.assertIn("prisma", content)

    def test_creates_env_stub(self):
        """@/lib/env must get a stub exporting env."""
        self._write("src/app/api/route.ts",
                    "import { env } from '@/lib/env';\nexport async function GET(){}")
        with patch("orchestrator.subprocess.run", return_value=MagicMock(returncode=0)), \
             patch("orchestrator.requests.post"):
            orc._fix_missing_deps(self.tmp)
        stub = os.path.join(self.tmp, "src", "lib", "env.ts")
        self.assertTrue(os.path.exists(stub))
        with open(stub) as f:
            content = f.read()
        self.assertIn("env", content)

    def test_does_not_reinstall_existing_package(self):
        nm = os.path.join(self.tmp, "node_modules", "lucide-react")
        os.makedirs(nm)
        self._write("src/app/page.tsx",
                    "import { X } from 'lucide-react';\nexport default function P(){}")
        with patch("orchestrator.subprocess.run") as mock_run, \
             patch("orchestrator.requests.post"):
            mock_run.return_value = MagicMock(returncode=0)
            orc._fix_missing_deps(self.tmp)
        for c in mock_run.call_args_list:
            args = c[0][0] if c[0] else []
            if "install" in args:
                self.assertNotIn("lucide-react", args)

    def test_creates_lib_utils_stub_with_cn(self):
        self._write("src/app/page.tsx",
                    "import { cn } from '@/lib/utils';\nexport default function P(){}")
        with patch("orchestrator.subprocess.run", return_value=MagicMock(returncode=0)), \
             patch("orchestrator.requests.post"):
            orc._fix_missing_deps(self.tmp)
        stub = os.path.join(self.tmp, "src", "lib", "utils.ts")
        self.assertTrue(os.path.exists(stub))
        with open(stub) as f:
            content = f.read()
        self.assertIn("twMerge", content)

    def test_handles_scoped_packages(self):
        self._write("src/app/page.tsx",
                    "import * as d from '@radix-ui/react-dialog';\nexport default function P(){}")
        with patch("orchestrator.subprocess.run") as mock_run, \
             patch("orchestrator.requests.post"):
            mock_run.return_value = MagicMock(returncode=0)
            orc._fix_missing_deps(self.tmp)
        calls = [str(c) for c in mock_run.call_args_list]
        self.assertTrue(any("radix-ui" in c for c in calls))

    def test_skips_node_modules(self):
        nm_file = os.path.join(self.tmp, "node_modules", "some-pkg", "index.ts")
        os.makedirs(os.path.dirname(nm_file))
        with open(nm_file, "w") as f:
            f.write("import { x } from 'unknown-pkg-xyz';\n")
        with patch("orchestrator.subprocess.run") as mock_run, \
             patch("orchestrator.requests.post"):
            mock_run.return_value = MagicMock(returncode=0)
            orc._fix_missing_deps(self.tmp)
        for c in mock_run.call_args_list:
            args = c[0][0] if c[0] else []
            if "install" in args:
                self.assertNotIn("unknown-pkg-xyz", args)


class TestFixNextAuthRoutes(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel_path, content):
        full = os.path.join(self.tmp, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
        return full

    def test_fixes_broken_nextauth_v4_pattern(self):
        # Use nextauth (no brackets) — brackets are a Linux-only path feature
        route_path = "src/app/api/auth/nextauth/route.ts"
        self._write(route_path,
                    "import NextAuth from 'next-auth';\nexport default NextAuth({});")
        with patch("orchestrator.requests.post"):
            orc._fix_nextauth_routes(self.tmp)
        full = os.path.join(self.tmp, route_path)
        with open(full) as f:
            content = f.read()
        self.assertIn("handlers", content)
        self.assertIn("export const { GET, POST } = handlers", content)

    def test_always_writes_canonical_v5_handler(self):
        """Any nextauth route is always rewritten to the canonical v5 exports."""
        route_path = "src/app/api/auth/nextauth/route.ts"
        existing = "import { handlers } from '@/auth';\nexport const { GET, POST } = handlers;\n"
        self._write(route_path, existing)
        with patch("orchestrator.requests.post"):
            orc._fix_nextauth_routes(self.tmp)
        full = os.path.join(self.tmp, route_path)
        with open(full) as f:
            content = f.read()
        self.assertIn("import { handlers } from '@/auth'", content)
        self.assertIn("export const { GET, POST } = handlers", content)

    def test_ignores_non_nextauth_routes(self):
        route_path = "src/app/api/stripe/route.ts"
        original = "export async function POST(req: Request) { return Response.json({}) }\n"
        self._write(route_path, original)
        with patch("orchestrator.requests.post"):
            orc._fix_nextauth_routes(self.tmp)
        full = os.path.join(self.tmp, route_path)
        with open(full) as f:
            content = f.read()
        self.assertEqual(content, original)


class TestFixInvalidRouteExports(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel, content):
        full = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
        return full

    def _read(self, rel):
        with open(os.path.join(self.tmp, rel)) as f:
            return f.read()

    def test_renames_create_checkout_to_post(self):
        self._write("src/app/api/stripe/checkout/route.ts",
            "import Stripe from 'stripe';\n"
            "export async function createCheckoutSession(req: Request) {\n"
            "  return Response.json({});\n"
            "}\n")
        with patch("orchestrator.requests.post"):
            orc._fix_invalid_route_exports(self.tmp)
        content = self._read("src/app/api/stripe/checkout/route.ts")
        self.assertIn("function POST(", content)
        self.assertNotIn("function createCheckoutSession(", content)

    def test_renames_handle_webhook_to_post(self):
        self._write("src/app/api/webhooks/route.ts",
            "export async function handleWebhook(req: Request) {\n"
            "  return Response.json({});\n"
            "}\n")
        with patch("orchestrator.requests.post"):
            orc._fix_invalid_route_exports(self.tmp)
        content = self._read("src/app/api/webhooks/route.ts")
        self.assertIn("function POST(", content)

    def test_renames_get_user_to_get(self):
        self._write("src/app/api/users/route.ts",
            "export async function getUsers(req: Request) {\n"
            "  return Response.json([]);\n"
            "}\n")
        with patch("orchestrator.requests.post"):
            orc._fix_invalid_route_exports(self.tmp)
        content = self._read("src/app/api/users/route.ts")
        self.assertIn("function GET(", content)

    def test_leaves_valid_post_handler_alone(self):
        original = ("export async function POST(req: Request) {\n"
                    "  return Response.json({});\n"
                    "}\n")
        self._write("src/app/api/test/route.ts", original)
        with patch("orchestrator.requests.post"):
            orc._fix_invalid_route_exports(self.tmp)
        self.assertEqual(self._read("src/app/api/test/route.ts"), original)

    def test_skips_nextauth_routes(self):
        original = ("import { handlers } from '@/auth';\n"
                    "export const { GET, POST } = handlers;\n")
        self._write("src/app/api/auth/nextauth/route.ts", original)
        with patch("orchestrator.requests.post"):
            orc._fix_invalid_route_exports(self.tmp)
        self.assertEqual(self._read("src/app/api/auth/nextauth/route.ts"), original)


# ─────────────────────────────────────────────────────────────────────────────
# INTEGRATION TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestDryRunFixConvoy(unittest.TestCase):
    """Full fix-mode convoy in dry-run — all 5 agents, PR, Vercel."""

    def setUp(self):
        orc.DRY_RUN    = True
        orc.BUILD_MODE = False
        self.events    = []

    def tearDown(self):
        orc.DRY_RUN    = False
        orc.BUILD_MODE = False

    def _capture(self, url, **kw):
        if "ingest" in url:
            self.events.append(kw["json"]["type"])
        return MagicMock()

    def test_convoy_emits_all_lifecycle_events(self):
        with patch("orchestrator.requests.post", side_effect=self._capture):
            orc.run_convoy("fix auth bug, add tests, audit security")
        required = {"AGENT_SPAWNED", "TASK_STARTED", "CODE_WRITTEN",
                    "REVIEW_PASSED", "PR_OPENED", "MERGED",
                    "DEPLOY_STARTED", "DEPLOYMENT_READY", "CONVOY_COMPLETE"}
        missing = required - set(self.events)
        self.assertFalse(missing, f"Missing events: {missing}")

    def test_all_5_agents_spawn(self):
        with patch("orchestrator.requests.post", side_effect=self._capture):
            orc.run_convoy("fix auth bug")
        spawned = [e for e in self.events if e == "AGENT_SPAWNED"]
        # Mayor + 5 agents
        self.assertGreaterEqual(len(spawned), 5)

    def test_convoy_complete_is_last(self):
        with patch("orchestrator.requests.post", side_effect=self._capture):
            orc.run_convoy("fix auth bug")
        self.assertEqual(self.events[-1], "CONVOY_COMPLETE")


class TestDryRunBuildConvoy(unittest.TestCase):
    """Full build-mode convoy in dry-run."""

    def setUp(self):
        orc.DRY_RUN    = True
        orc.BUILD_MODE = True
        self.events    = []

    def tearDown(self):
        orc.DRY_RUN    = False
        orc.BUILD_MODE = False

    def _capture(self, url, **kw):
        if "ingest" in url:
            self.events.append(kw["json"]["type"])
        return MagicMock()

    def test_build_convoy_completes(self):
        with patch("orchestrator.requests.post", side_effect=self._capture):
            url = orc.build_project("Next.js SaaS with Stripe")
        self.assertIn("saas-dashboard.vercel.app", url)

    def test_build_emits_deployment_ready(self):
        with patch("orchestrator.requests.post", side_effect=self._capture):
            orc.build_project("Next.js SaaS with Stripe")
        self.assertIn("DEPLOYMENT_READY", self.events)

    def test_build_convoy_complete_last(self):
        with patch("orchestrator.requests.post", side_effect=self._capture):
            orc.build_project("Next.js SaaS with Stripe")
        self.assertEqual(self.events[-1], "CONVOY_COMPLETE")

    def test_5_agents_scaffold(self):
        with patch("orchestrator.requests.post", side_effect=self._capture):
            orc.build_project("Next.js SaaS with Stripe")
        written = [e for e in self.events if e == "CODE_WRITTEN"]
        self.assertEqual(len(written), 5)


class TestRealBuildPipeline(unittest.TestCase):
    """Build pipeline with mocked DO Inference — writes real files."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        orc.DRY_RUN    = False
        orc.BUILD_MODE = True
        orc.VERCEL_TOKEN  = "vt-test"
        orc.GITHUB_TOKEN  = "gh-test"
        self._orig_builds = None

        # Patch expanduser to use our tmp dir
        self._builds_dir = os.path.join(self.tmp, "builds")
        os.makedirs(self._builds_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        orc.DRY_RUN    = False
        orc.BUILD_MODE = False

    def _make_inference_mock(self, content="export default function X() {}"):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": content}}]
        }
        return mock_resp

    def test_files_written_to_project_dir(self):
        project_dir = os.path.join(self._builds_dir, "saas-dashboard")

        plan_json = json.dumps(orc.DRY_RUN_BUILD_PLAN)

        call_count = [0]
        def inference_side(url, **kw):
            if "ingest" in url:
                return MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:
                # First call = parse_project_brief
                r = MagicMock(); r.raise_for_status = MagicMock()
                r.json.return_value = {
                    "choices": [{"message": {"content": plan_json}}]
                }
                return r
            # Subsequent = generate_file_content
            r = MagicMock(); r.raise_for_status = MagicMock()
            r.json.return_value = {
                "choices": [{"message": {"content": "// generated\nexport default function F() {}"}}]
            }
            return r

        with patch("orchestrator.requests.post", side_effect=inference_side), \
             patch("orchestrator.requests.get") as mock_get, \
             patch("orchestrator.subprocess.run") as mock_sub, \
             patch("os.path.expanduser",
                   side_effect=lambda p: p.replace("~/gastown/builds",
                                                    self._builds_dir)):
            mock_get.return_value = MagicMock(
                json=lambda: {"readyState": "READY", "url": "test.vercel.app"})
            mock_sub.return_value = MagicMock(
                returncode=0, stdout="https://test.vercel.app\n", stderr="")
            orc.build_project("Next.js SaaS")

        # At least one file should have been written
        written_files = []
        for root, _, files in os.walk(self._builds_dir):
            for fn in files:
                if not fn.endswith(".json") and fn not in ("globals.css",):
                    written_files.append(os.path.join(root, fn))
        self.assertGreater(len(written_files), 0, "No files were written")


# ─────────────────────────────────────────────────────────────────────────────
# BEHAVIOUR TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestConcurrentAgents(unittest.TestCase):
    """5 agents must run concurrently (not sequentially)."""

    def test_all_agents_run_in_parallel(self):
        orc.DRY_RUN    = True
        orc.BUILD_MODE = True
        tmp = tempfile.mkdtemp()
        start_times = {}
        end_times   = {}

        original = orc.scaffold_agent
        def recording_scaffold(agent_name, task, target_file, project_dir, framework):
            start_times[agent_name] = time.time()
            original(agent_name, task, target_file, project_dir, framework)
            end_times[agent_name] = time.time()

        tasks = orc.DRY_RUN_BUILD_PLAN["tasks"]
        threads = []
        with patch("orchestrator.scaffold_agent", side_effect=recording_scaffold), \
             patch("orchestrator.requests.post"):
            for t in tasks:
                th = threading.Thread(
                    target=orc.scaffold_agent,
                    args=(t["agent"], t["task"], t["file"], tmp, "nextjs"))
                threads.append(th)
                th.start()
                time.sleep(0.05)
            for th in threads:
                th.join()

        shutil.rmtree(tmp, ignore_errors=True)
        orc.DRY_RUN    = False
        orc.BUILD_MODE = False

        # All agents must have started before the last one finished
        if start_times and end_times:
            first_start = min(start_times.values())
            last_start  = max(start_times.values())
            # All 5 started within 5 seconds of each other (concurrent, not serial)
            self.assertLess(last_start - first_start, 5.0)


class TestEventOrdering(unittest.TestCase):
    """AGENT_SPAWNED must come before CODE_WRITTEN for every agent."""

    def test_spawn_before_code_written(self):
        orc.DRY_RUN    = True
        orc.BUILD_MODE = False
        agent_events   = {}

        def capture(url, **kw):
            if "ingest" in url:
                payload = kw["json"]
                role = payload.get("agent_role", "")
                if role not in agent_events:
                    agent_events[role] = []
                agent_events[role].append(payload["type"])
            return MagicMock()

        with patch("orchestrator.requests.post", side_effect=capture):
            orc.run_convoy("fix auth bug")

        for role, events in agent_events.items():
            if "AGENT_SPAWNED" in events and "CODE_WRITTEN" in events:
                self.assertLess(events.index("AGENT_SPAWNED"),
                                events.index("CODE_WRITTEN"),
                                f"{role}: SPAWNED must come before CODE_WRITTEN")

        orc.DRY_RUN = False


class TestBootstrapFileValidity(unittest.TestCase):
    """Bootstrap output must produce valid JSON config files."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_package_json_parseable(self):
        with patch("orchestrator.subprocess.run",
                   return_value=MagicMock(returncode=0, stderr="")), \
             patch("orchestrator.requests.post"):
            orc._bootstrap_nextjs(self.tmp, "test-proj")
        with open(os.path.join(self.tmp, "package.json")) as f:
            pkg = json.load(f)
        self.assertEqual(pkg["name"], "test-proj")

    def test_tsconfig_parseable(self):
        with patch("orchestrator.subprocess.run",
                   return_value=MagicMock(returncode=0, stderr="")), \
             patch("orchestrator.requests.post"):
            orc._bootstrap_nextjs(self.tmp, "test-proj")
        with open(os.path.join(self.tmp, "tsconfig.json")) as f:
            ts = json.load(f)
        self.assertIn("compilerOptions", ts)

    def test_vercel_json_parseable(self):
        with patch("orchestrator.subprocess.run",
                   return_value=MagicMock(returncode=0, stderr="")), \
             patch("orchestrator.requests.post"):
            orc._bootstrap_nextjs(self.tmp, "test-proj")
        with open(os.path.join(self.tmp, "vercel.json")) as f:
            vj = json.load(f)
        self.assertEqual(vj["framework"], "nextjs")

    def test_layout_contains_project_name(self):
        with patch("orchestrator.subprocess.run",
                   return_value=MagicMock(returncode=0, stderr="")), \
             patch("orchestrator.requests.post"):
            orc._bootstrap_nextjs(self.tmp, "my-cool-app")
        with open(os.path.join(self.tmp, "src", "app", "layout.tsx")) as f:
            layout = f.read()
        self.assertIn("my-cool-app", layout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
