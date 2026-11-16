"""Verify the container and the sandbox used by the actual shared launcher."""
import asyncio
import os
import shlex
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import SANDBOX_DISABLE_FLAGS, launch  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402


def verify_sandbox(status, command_line):
    """Fail closed if Chromium's internal pages cannot establish sandboxing."""
    if not command_line.strip():
        raise RuntimeError("Could not read Chromium's effective command line")
    disabled = [arg for arg in shlex.split(command_line)
                if arg.split("=", 1)[0] in SANDBOX_DISABLE_FLAGS]
    if disabled:
        raise RuntimeError("Sandbox-disabling launch flags: " + ", ".join(disabled))
    if status.get("Layer 1 Sandbox") not in ("Namespace", "SUID"):
        raise RuntimeError("Chromium does not report an active layer-1 sandbox")
    for feature in ("PID namespaces", "Network namespaces", "Seccomp-BPF sandbox"):
        if status.get(feature) != "Yes":
            raise RuntimeError(f"Chromium sandbox feature is not active: {feature}")


async def main():
    if os.geteuid() == 0:
        raise RuntimeError("Crawler must run as a non-root user")
    print(f"OK: non-root uid {os.geteuid()}", flush=True)
    for path in (Path("/var/run/docker.sock"), Path("/app/.env")):
        if path.exists():
            raise RuntimeError(f"Unexpected container exposure: {path}")
        print(f"OK: {path} absent", flush=True)
    caps = next(line.split()[1] for line in Path("/proc/self/status").read_text().splitlines()
                if line.startswith("CapEff:"))
    if int(caps, 16):
        raise RuntimeError("Expected cap_drop ALL: effective capabilities are not empty")
    print("OK: effective container capabilities are empty", flush=True)

    async with async_playwright() as pw:
        with tempfile.TemporaryDirectory(prefix="tm_preflight_") as profile:
            ctx = await launch(pw, profile)
            try:
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                await page.goto("chrome://version/")
                command_line = await page.locator("#command_line").inner_text(timeout=5000)
                await page.goto("chrome://sandbox/")
                status = await page.locator("tr").evaluate_all("""rows => Object.fromEntries(
                    rows.map(r => Array.from(r.cells, c => c.textContent.trim()))
                        .filter(cells => cells.length === 2))""")
                verify_sandbox(status, command_line)
                print("OK: no sandbox-disabling launch flags", flush=True)
                for feature in ("Layer 1 Sandbox", "PID namespaces", "Network namespaces", "Seccomp-BPF sandbox"):
                    print(f"OK: {feature}: {status[feature]}", flush=True)
            finally:
                await ctx.close()
    print("PREFLIGHT PASSED", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
