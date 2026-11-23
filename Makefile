COMPOSE     = docker compose
COMPOSE_VPN = docker compose -f docker-compose.yml -f docker-compose.vpn.yml
COMPOSE_TEST = docker compose -f docker-compose.yml -f docker-compose.test.yml
DB          = data/crawl.sqlite
RESPAWN_DB  = data/respawn.sqlite
RESULTS     = results
STUDY_SITES ?= data/sites_ranked.csv
STUDY_BATCH ?= study
SITE        ?= theguardian.com
ACTION      ?= reject

.PHONY: build dirs preflight preflight-vpn smoke extensions verify-blockers verify-consent consent-check pilot-baseline pilot-conditions baseline baseline2 blockers ubol pbadger optout accept respawn analyze

dirs:
	mkdir -p data ext results

build: dirs
	$(COMPOSE) build

# --- Isolation pre-flight (run before any real crawl) ---
preflight: build
	$(COMPOSE) run --rm crawler python scripts/preflight.py
	@echo "[5] mounts (expect only data rw, ext ro, results rw):"; $(COMPOSE) config | grep -A8 'volumes:'

# Needs .env with WIREGUARD_CONFIG_PATH. Run before crawling: briefly stops VPN.
preflight-vpn:
	python3 scripts/verify_vpn.py

# Small diagnostic sample, separate data/results, one browser at a time.
pilot-baseline: dirs
	python3 scripts/run_pilot.py --modes baseline

pilot-conditions: dirs
	python3 scripts/run_pilot.py --modes baseline2 optout accept ubol pbadger

# --- Smoke test (no VPN; fixture server inside the container) ---
smoke: dirs build
	$(COMPOSE) run --rm crawler python -m unittest discover -s test -p 'test_*.py'
	$(COMPOSE) run --rm crawler python test/smoke.py

extensions:
	bash scripts/get_extensions.sh

verify-blockers: dirs build
	$(COMPOSE_TEST) run --rm crawler python -u test/verify_blockers.py

verify-consent: dirs build
	$(COMPOSE_TEST) run --rm crawler python -m unittest discover -s test -p 'test_consent*.py'
	$(COMPOSE_TEST) run --rm crawler python -u test/verify_consent_frames.py

# Fresh DB and timestamped evidence every time; completed pilot rows are untouched.
consent-check: dirs build
	python3 scripts/check_consent.py --site "$(SITE)" --action "$(ACTION)"

# --- Full crawl runs (use COMPOSE_VPN; ensure .env is configured) ---
baseline: dirs
	python3 scripts/run_study.py --sites $(STUDY_SITES) --batch $(STUDY_BATCH) --modes baseline

baseline2: dirs
	python3 scripts/run_study.py --sites $(STUDY_SITES) --batch $(STUDY_BATCH) --modes baseline2

# blockers = ubol + pbadger (sequential, shared DB)
blockers: ubol pbadger

ubol: dirs
	python3 scripts/run_study.py --sites $(STUDY_SITES) --batch $(STUDY_BATCH) --modes ubol

pbadger: dirs
	python3 scripts/run_study.py --sites $(STUDY_SITES) --batch $(STUDY_BATCH) --modes pbadger

optout: dirs
	python3 scripts/run_study.py --sites $(STUDY_SITES) --batch $(STUDY_BATCH) --modes optout

accept: dirs
	python3 scripts/run_study.py --sites $(STUDY_SITES) --batch $(STUDY_BATCH) --modes accept

respawn: dirs
	$(COMPOSE) run --rm crawler python scripts/pick_respawn_sites.py data/$(STUDY_BATCH)/crawl.sqlite --run baseline --out data/$(STUDY_BATCH)/respawn_sites.csv
	$(COMPOSE_VPN) run --rm crawler \
	    xvfb-run -a python respawn.py --sites data/$(STUDY_BATCH)/respawn_sites.csv --clear cookies \
	    --out data/$(STUDY_BATCH)/respawn.sqlite --workers 1 --headful
	$(COMPOSE_VPN) run --rm crawler \
	    xvfb-run -a python respawn.py --sites data/$(STUDY_BATCH)/respawn_sites.csv --clear all \
	    --out data/$(STUDY_BATCH)/respawn.sqlite --workers 1 --headful

analyze: dirs
	$(COMPOSE) run --rm crawler \
	    python analyze.py data/$(STUDY_BATCH)/crawl.sqlite --baseline baseline --outdir results/$(STUDY_BATCH)

# --- Helpers ---
# Headful run example (run from inside the container or on the host with X11):
#   docker compose run --rm crawler xvfb-run -a python crawler.py --sites sites.csv --run baseline --out data/crawl.sqlite --workers 1 --headful

# Verify egress IP (should be your VPN exit, not your home IP):
#   docker compose -f docker-compose.yml -f docker-compose.vpn.yml run --rm crawler \
#     python -c "import urllib.request; print(urllib.request.urlopen('https://ifconfig.me').read().decode())"
