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

        print("consent failures and unknown state ...")
        consent_sites = td / "sites_consent.csv"
        consent_sites.write_text("domain,category\nhttp://broken.test/,test\n"
                                 "http://unknown.test/,test\nhttp://incomplete.test/,test\n"
                                 "http://paywall.test/,test\n")
        extra = ["--sites", str(consent_sites), "--dwell", "1", "--subpages", "0", "--consent-wait", "1"]
        run(*crawl, *extra, "--run", "baseline")
        run(*crawl, *extra, "--run", "optout", "--consent", "reject", "--gpc")

        print("failed navigations (HTTP 404 and 503) ...")
        failed_sites = td / "sites_failed.csv"
        failed_sites.write_text("domain,category\nhttp://news.test/__http404,test\n"
                                "http://news.test/__http503,test\n")
        run(*crawl, "--sites", str(failed_sites), "--run", "failed_navigation")

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
        coverage = pd.read_csv(f"{out}/consent_coverage.csv")

        print("\nAssertions:")

        # Failed document loads must remain diagnostic rows, never successful scans.
        ccon = sqlite3.connect(db)
        failed = ccon.execute("SELECT site,ok,error FROM sites_done WHERE run='failed_navigation'").fetchall()
        check(len(failed) == 2 and all(ok == 0 and error for _, ok, error in failed),
              "HTTP failures are marked unsuccessful with error details")
        statuses = ccon.execute("SELECT status,error FROM visits WHERE run='failed_navigation'").fetchall()
        check(sorted(status for status, _ in statuses) == [404, 503]
              and all(error for _, error in statuses), "failed visits retain HTTP status and error")
        ccon.close()
        check(not (cmp["run"] == "failed_navigation").any(),
              "failed navigations are excluded from survival comparisons")
        check((ps[ps["run"] == "failed_navigation"]["measurement_ok"] == 0).all(),
              "failed navigations are flagged unusable in per_site.csv")

        for host, outcome in (("broken.test", "failed"), ("unknown.test", "unknown"),
                              ("incomplete.test", "failed"), ("paywall.test", "failed")):
            site = f"http://{host}/"
            row = ps[(ps["run"] == "optout") & (ps["site"] == site)].iloc[0]
            check(row["ok"] == 1 and row["measurement_ok"] == 1,
                  f"{host}: browsing observations remain usable")
            check(row["consent_outcome"] == outcome and row["comparison_eligible"] == 0,
                  f"{host}: consent is {outcome}, excluded from verified comparisons")
            check(row["fp_scripts"] > 0, f"{host}: raw tracking observations retained")
            check(not ((cmp["run"] == "optout") & (cmp["site"] == site)).any(),
                  f"{host}: no misleading opt-out survival result")
        oo_ps = ps[(ps["run"] == "optout") & (ps["site"] == SITE_KEY)].iloc[0]
        check(oo_ps["consent_outcome"] == "confirmed" and oo_ps["gpc"] == "enabled",
              "working rejection is verified and GPC is recorded separately")
        acc_ps = ps[(ps["run"] == "accept") & (ps["site"] == SITE_KEY)].iloc[0]
        check(acc_ps["consent_outcome"] == "confirmed", "working acceptance is verified")
        oo_coverage = coverage[coverage["run"] == "optout"].set_index("consent_outcome")
        check(int(oo_coverage.loc["confirmed", "site_runs"]) == 1
              and int(oo_coverage.loc["failed", "site_runs"]) == 3
              and int(oo_coverage.loc["unknown", "site_runs"]) == 1,
              "consent coverage reports confirmed, failed and unknown cases")
        pay = ps[(ps["run"] == "optout")].set_index("site")["consent_pay_or_ok"]
        check(pay.get("http://paywall.test/") == 1 and pay.drop("http://paywall.test/").sum() == 0,
              "pay-or-OK wall is classified separately, other failures are not")
        check(int(oo_coverage.loc["failed", "pay_or_ok"]) == 1, "coverage counts pay-or-OK walls")

        # 1. All 6 techniques detected in the baseline post-consent phases
        b = fps[(fps["run"] == "baseline") & (fps["phase_group"] == "post")]
        for tech in ("canvas", "canvas_font", "webrtc", "audio", "webgl", "attr_enum"):
            check(tech in b["technique"].values, f"baseline detects {tech}")
        check("tracker.test" in b["script_domain"].values,
              "baseline attributes to tracker.test (eTLD+1 of cdn.tracker.test)")

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

        # Exfiltration: the canvas value leaves in a beacon body; blocked tracker sends nothing.
        exf = pd.read_csv(f"{out}/fp_exfil.csv")
        b_ex = exf[(exf["run"] == "baseline") & (exf["site"] == SITE_KEY)]
        check(((b_ex["kind"] == "canvas") & (b_ex["receiver_domain"] == "tracker.test")
               & (b_ex["encoding"] == "base64_slice")).any(),
              "baseline: canvas value found in a request to tracker.test")
        if (b_ex["kind"] == "webgl_renderer").any():
            print("  OK  baseline: WebGL renderer string found in a request (WebGL available)")
        else:
            print("  --  WebGL renderer not exfiltrated (no WebGL in this browser); not asserted")
        check(not (exf["run"] == "blocker").any(), "blocker: no fingerprint value leaves the browser")
        check(float(cmp[cmp["run"] == "blocker"]["exfil_survival"].iloc[0]) == 0.0,
              "blocker: exfiltration survival = 0.0")
        check(float(cmp[cmp["run"] == "optout"]["exfil_survival"].iloc[0]) == 1.0,
              "optout: exfiltration survival = 1.0 (tracker ignores consent)")

        # ID sharing: the first-party _trk cookie is sent to the tracker's domain.
        shr = pd.read_csv(f"{out}/id_sharing.csv")
        check(((shr["run"] == "baseline") & (shr["cookie_name"] == "_trk")
               & (shr["receiver_domain"] == "tracker.test")).any(),
              "baseline: _trk identifier shared with tracker.test")
        check(not ((shr["cookie_name"] == "consent")).any(), "consent cookie not reported as an identifier")

        # Behavioural listeners registered by the tracker.
        beh = pd.read_csv(f"{out}/behavior.csv")
        tb = beh[(beh["run"] == "baseline") & (beh["script_domain"] == "tracker.test")]
        check(len(tb) and {"keyboard", "pointer"} <= set(",".join(tb["families"]).split(",")),
              "baseline: tracker.test registers pointer and keyboard listeners")
        check(int(ps[(ps["run"] == "baseline") & (ps["site"] == SITE_KEY)]["behavior_tp_multi_family"].iloc[0]) >= 1,
              "per_site counts third parties with >=2 behavioural families")

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
