"""Supervise the two development HTTP services without orphaning descendants."""
import os
import signal
import subprocess
import sys
import time


def supervise(commands, grace=3.0):
    children = []
    interrupted = []
    previous = {}

    def request_stop(signum, _frame):
        interrupted.append(signum)

    def stop_children():
        # Each child owns a new process group, including npm's shell and Vite.
        for _, child in children:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + grace
        while any(child.poll() is None for _, child in children) and time.monotonic() < deadline:
            time.sleep(0.025)
        for _, child in children:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()

    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.signal(sig, request_stop)
        for name, command in commands:
            children.append((name, subprocess.Popen(command, start_new_session=True)))
        print("Development services supervised; either service exiting stops its sibling.", flush=True)
        while not interrupted:
            for name, child in children:
                code = child.poll()
                if code is not None:
                    print(f"Development service {name} exited ({code}); stopping both services.", flush=True)
                    return 1  # Even unexpected exit 0 must not leave a partial preview.
            time.sleep(0.05)
        print("Development stop requested; terminating both service process groups.", flush=True)
        return 128 + interrupted[0]
    finally:
        stop_children()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    # Django is reached only through the Vite proxy, so it always stays on
    # loopback. Vite binds to loopback too unless DEV_BIND_HOST opts in to a
    # wider interface (for example a container that needs 0.0.0.0).
    vite_host = os.environ.get("DEV_BIND_HOST", "127.0.0.1")
    raise SystemExit(supervise([
        ("Django", [sys.executable, "manage.py", "runserver", os.environ.get("DJANGO_BIND_ADDRESS", "127.0.0.1:8000"), "--noreload"]),
        ("Vite", ["npm", "run", "dev", "--", "--host", vite_host, "--port", "5000", "--strictPort"]),
    ]))
