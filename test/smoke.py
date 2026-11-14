#!/usr/bin/env python3
"""End-to-end smoke test against the local fixture server.
Starts the fixture server on 127.0.0.1:8765, runs all crawl modes + respawn,
then asserts the expected results from §3 of the debrief.

Run from the repo root (or inside the Docker container via 'make smoke'):
    python test/smoke.py [--keep]

Passes on success; exits non-zero on the first failed assertion.
"""
import argparse
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
RESOLVER = "--host-resolver-rules=MAP *.test 127.0.0.1:8765"
SITES = str(HERE / "sites_test.csv")
SITE_KEY = "http://news.test/"   # domain value as stored in visits.site


def run(*cmd):
    r = subprocess.run(list(cmd), check=False)
    if r.returncode != 0:
        sys.exit(f"\nSMOKE FAIL – command exited {r.returncode}:\n  {' '.join(str(x) for x in cmd)}")


def check(cond, msg):
    if not cond:
        sys.exit(f"\nSMOKE FAIL – assertion: {msg}")
    print(f"  OK  {msg}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--keep", action="store_true", help="keep temp DB after run")
    args = ap.parse_args()

    td = Path(tempfile.mkdtemp(prefix="tm_smoke_"))
    db = str(td / "crawl.sqlite")
    rdb = str(td / "respawn.sqlite")
    out = str(td / "results")
    Path(out).mkdir(parents=True)

    srv = subprocess.Popen(
        [sys.executable, str(HERE / "server.py"), "8765"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(1.0)

    crawl = [
        sys.executable, str(ROOT / "crawler.py"),
        "--sites", SITES, "--out", db,
        "--workers", "1", "--dwell", "3", "--timeout", "30", "--subpages", "2",
        "--chromium-arg", RESOLVER,
    ]
    resp = [
        sys.executable, str(ROOT / "respawn.py"),
        "--sites", SITES, "--out", rdb,
        "--dwell", "5", "--timeout", "30", "--workers", "1",
        "--chromium-arg", RESOLVER,
    ]

    try:
        print("baseline ...")
        run(*crawl, "--run", "baseline")

        print("blocker (ext_block) ...")
        run(*crawl, "--run", "blocker", "--extension", str(HERE / "ext_block"))

        print("optout (reject + GPC) ...")
        run(*crawl, "--run", "optout", "--consent", "reject", "--gpc")

        print("accept ...")
        run(*crawl, "--run", "accept", "--consent", "accept")

        print("respawn --clear cookies ...")
        run(*resp, "--clear", "cookies")

        print("respawn --clear all ...")
        run(*resp, "--clear", "all")

        print("analyze ...")
        run(sys.executable, str(ROOT / "analyze.py"), db,
            "--baseline", "baseline", "--outdir", out)

        # ---- assertions ------------------------------------------------
        import pandas as pd

        fps = pd.read_csv(f"{out}/fp_scripts.csv")
        ps  = pd.read_csv(f"{out}/per_site.csv")
        cmp = pd.read_csv(f"{out}/comparison.csv")

        print("\nAssertions:")

        # 1. All 6 techniques detected in the baseline post-consent phases
        b = fps[(fps["run"] == "baseline") & (fps["phase_group"] == "post")]
        for tech in ("canvas", "canvas_font", "webrtc", "audio", "webgl", "attr_enum"):
            check(tech in b["technique"].values, f"baseline detects {tech}")
        check("cdn.tracker.test" in b["script_domain"].values,
              "baseline attributes to cdn.tracker.test")

        # 2. Blocker: survival = 0.0, blocked_requests > 0
        bl_cmp = cmp[cmp["run"] == "blocker"]
        check(len(bl_cmp) > 0, "blocker row present in comparison.csv")
        surv = float(bl_cmp["survival"].iloc[0])
        check(surv == 0.0, f"blocker survival = 0.0 (got {surv})")
        bl_ps = ps[ps["run"] == "blocker"]
        blocked = int(bl_ps["blocked_requests"].iloc[0])
        check(blocked > 0, f"blocker blocked_requests > 0 (got {blocked})")

        # 3. Opt-out: survival = 1.0 (fixture tracker ignores consent)
        oo_cmp = cmp[cmp["run"] == "optout"]
        check(len(oo_cmp) > 0, "optout row present in comparison.csv")
        surv_oo = float(oo_cmp["survival"].iloc[0])
        check(surv_oo == 1.0, f"optout survival = 1.0 (got {surv_oo})")

        # 4. Accept: consent-respecting ads.test script now fires (canvas technique)
        acc_fps = fps[(fps["run"] == "accept") & (fps["phase_group"] == "post")]
        check("ads.test" in acc_fps["script_domain"].values,
              "accept: ads.test canvas detected (consent-respecting script fires)")

        # 5. Respawn --clear cookies: _trk token survives (backed up in localStorage)
        rcon = sqlite3.connect(rdb)
        rows_ck = rcon.execute(
            "SELECT a1_cookie, verdict FROM respawned WHERE mode='cookies'"
        ).fetchall()
        check(any("_trk@" in (r[0] or "") for r in rows_ck),
              "respawn(cookies): _trk token survives cookie clear")

        # 6. Respawn --clear all: _fpid identified as fingerprint-derived
        rows_all = rcon.execute(
            "SELECT a1_cookie, verdict FROM respawned WHERE mode='all'"
        ).fetchall()
        check(any("_fpid@" in (r[0] or "") for r in rows_all),
              "respawn(all): _fpid token is fingerprint-derived")
        rcon.close()

        print("\n=== ALL SMOKE ASSERTIONS PASSED ===\n")

    finally:
        srv.terminate()
        srv.wait()
        if args.keep:
            print(f"Results kept at {td}")
        else:
            shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    main()
