"""No database/network: startup write guards and real OS process supervision."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


def wait_for(predicate, timeout=5):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(0.025)
    raise AssertionError("Timed out waiting for isolated test process")


def alive(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except FileNotFoundError:
        return False


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="tivoli-supervision-test-")
        self.root = Path(self.directory.name)
        self.processes = []

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=8)
        self.directory.cleanup()

    def worker(self, name, grandchild=False):
        path = self.root / name
        code = "import os,pathlib,sys,time,subprocess; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
        if grandchild:
            code += "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); pathlib.Path(sys.argv[1]+'.child').write_text(str(p.pid)); "
        code += "time.sleep(60)"
        return [sys.executable, "-c", code, str(path)]

    def launch(self, commands):
        code = "import json,sys; from scripts.dev_services import supervise; sys.exit(supervise(json.loads(sys.argv[1]),grace=.5))"
        process = subprocess.Popen([sys.executable, "-c", code, json.dumps(commands)], cwd=ROOT,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.processes.append(process)
        return process

    def assert_stopped(self, *names):
        for name in names:
            path = self.root / name
            self.assertTrue(path.exists())
            wait_for(lambda: not alive(int(path.read_text())))

    def test_unexpected_clean_exit_stops_sibling(self):
        process = self.launch([["long", self.worker("long")], ["exit", [sys.executable, "-c", "import time; time.sleep(.3)"]]])
        self.assertEqual(process.wait(timeout=8), 1)
        self.assert_stopped("long")

    def test_failure_stops_sibling_and_grandchild(self):
        process = self.launch([["long", self.worker("long", True)], ["exit", [sys.executable, "-c", "import time; time.sleep(.4); raise SystemExit(23)"]]])
        self.assertEqual(process.wait(timeout=8), 1)
        self.assert_stopped("long", "long.child")

    def test_both_operator_signals_stop_owned_process_groups(self):
        for signum in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signal=signum):
                process = self.launch([["one", self.worker("one", True)], ["two", self.worker("two")]])
                wait_for(lambda: all((self.root / name).exists() for name in ("one.child", "two")))
                process.send_signal(signum)
                self.assertEqual(process.wait(timeout=8), 128 + signum)
                self.assert_stopped("one", "one.child", "two")
                for path in self.root.iterdir():
                    path.unlink()

    def test_spawn_failure_returns_failure(self):
        process = self.launch([["bad", [str(self.root / "nonexistent-command")]]])
        self.assertNotEqual(process.wait(timeout=8), 0)

    def test_two_start_stop_lifecycles_leave_no_live_children(self):
        for cycle in range(2):
            name = f"cycle-{cycle}"
            process = self.launch([["service", self.worker(name)]])
            wait_for(lambda: (self.root / name).exists())
            process.terminate()
            self.assertEqual(process.wait(timeout=8), 143)
            self.assert_stopped(name)


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="tivoli-startup-test-")
        self.root = Path(self.directory.name)
        (self.root / "scripts").mkdir()
        (self.root / "bin").mkdir()
        (self.root / "data").mkdir()
        (self.root / "data/PG_VERSION").write_text("16")
        (self.root / "scripts/dev.sh").write_text((ROOT / "scripts/dev.sh").read_text())
        (self.root / "scripts/demo_startup_check.py").write_text("# stdin placeholder")
        (self.root / "scripts/local_postgres.sh").write_text('ensure_local_postgres() { echo ensure >> "$TRACE"; }\n')
        self.stub("psql", 'echo psql >> "$TRACE"\nif [[ "${MISSING_DB:-0}" != 1 ]]; then echo tivoli_erp_demo; fi\n')
        self.stub("python", 'echo "$*" >> "$TRACE"\nif [[ "$*" == "manage.py migrate --check" && "${PENDING:-0}" == 1 ]]; then exit 1; fi\n')
        for forbidden in ("createdb", "initdb"):
            self.stub(forbidden, f'echo FORBIDDEN-{forbidden} >> "$TRACE"; exit 91\n')
        self.env = {**os.environ, "APP_ENV": "demo", "DB_HOST": "127.0.0.1",
                    "DB_PORT": "59999", "DB_NAME": "tivoli_erp_demo", "DB_USER": "fictional",
                    "USE_BUNDLED_POSTGRES": "1", "PGDATA": str(self.root / "data"),
                    "TRACE": str(self.root / "trace"), "PATH": f'{self.root / "bin"}:{os.environ["PATH"]}'}
        self.env.pop("REPLIT_DEV_DOMAIN", None)

    def tearDown(self):
        self.directory.cleanup()

    def stub(self, name, body):
        path = self.root / "bin" / name
        path.write_text("#!/usr/bin/env bash\nset -eu\n" + body)
        path.chmod(0o700)

    def run_script(self, **overrides):
        result = subprocess.run(["bash", "scripts/dev.sh"], cwd=self.root,
                                env={**self.env, **overrides}, capture_output=True, text=True)
        path = self.root / "trace"
        trace = path.read_text() if path.exists() else ""
        return result, trace

    def test_existing_startup_checks_without_seed_migration_or_creation(self):
        result, trace = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("manage.py migrate --check", trace)
        self.assertIn("manage.py shell", trace)
        self.assertIn("scripts/dev_services.py", trace)
        self.assertNotIn("seed_demo", trace)
        self.assertNotIn("migrate --noinput", trace)
        self.assertNotIn("FORBIDDEN", trace)

    def test_generated_secret_key_is_private_and_reused_across_restarts(self):
        self.env.pop("DJANGO_SECRET_KEY", None)
        self.env.pop("SESSION_SECRET", None)
        result, _ = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        key_file = self.root / ".runtime/django-secret-key"
        first = key_file.read_text()
        self.assertGreaterEqual(len(first), 60)
        self.assertEqual(key_file.stat().st_mode & 0o077, 0)
        result, _ = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(key_file.read_text(), first)

    def test_wildcard_allowed_hosts_are_refused(self):
        result, trace = self.run_script(DJANGO_ALLOWED_HOSTS="*")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("wildcards are refused", result.stderr)
        self.assertEqual(trace, "")

    def test_missing_cluster_is_not_initialized(self):
        result, trace = self.run_script(PGDATA=str(self.root / "absent"))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(trace, "")

    def test_missing_database_is_not_created(self):
        result, trace = self.run_script(MISSING_DB="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("FORBIDDEN", trace)
        self.assertNotIn("manage.py", trace)

    def test_pending_migration_blocks_services_without_applying(self):
        result, trace = self.run_script(PENDING="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stopped at read-only schema readiness", result.stderr)
        self.assertIn("No migration or seed was performed", result.stderr)
        self.assertIn("manage.py showmigrations foundation --plan", trace)
        self.assertNotIn("migrate --noinput", trace)
        self.assertNotIn("manage.py shell", trace)
        self.assertNotIn("dev_services.py", trace)
        self.assertNotIn("seed_demo", trace)

    def test_remote_host_rejected_before_bootstrap(self):
        result, trace = self.run_script(DB_HOST="not-local.invalid")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(trace, "")

    def test_non_demo_environment_rejected_before_bootstrap(self):
        result, trace = self.run_script(APP_ENV="production")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(trace, "")


if __name__ == "__main__":
    unittest.main()
