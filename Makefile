COMPOSE     = docker compose
COMPOSE_VPN = docker compose -f docker-compose.yml -f docker-compose.vpn.yml
COMPOSE_TEST = docker compose -f docker-compose.yml -f docker-compose.test.yml
DB          = data/crawl.sqlite
RESPAWN_DB  = data/respawn.sqlite
RESULTS     = results
# Container user = host user, so bind-mounted data/ and results/ are writable.
export PW_UID := $(shell id -u)
export PW_GID := $(shell id -g)
SITE        ?= theguardian.com
ACTION      ?= reject
FINAL_SITES ?= sites_final_experiment.csv
FINAL_SEED ?= 42

.PHONY: build dirs preflight preflight-vpn smoke extensions verify-blockers verify-consent final-study-prepare consent-check pilot-baseline pilot-conditions baseline baseline2 blockers ubol pbadger optout accept rejectonly gpconly baseline3 respawn analyze stats validation-sample

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

# Freeze one randomized four-condition plan; this does not start live visits.
final-study-prepare: dirs
	python3 scripts/run_repeated_validation.py --sites "$(FINAL_SITES)" --repeats 1 --seed "$(FINAL_SEED)" --final-study

# Fresh DB and timestamped evidence every time; completed pilot rows are untouched.
consent-check: dirs build
	python3 scripts/check_consent.py --site "$(SITE)" --action "$(ACTION)"

# --- Full crawl runs (use COMPOSE_VPN; ensure .env is configured) ---
baseline: dirs
	$(COMPOSE_VPN) run --rm crawler \
	    python crawler.py --sites sites.csv --run baseline \
	    --out $(DB) --workers 4

baseline2: dirs
	$(COMPOSE_VPN) run --rm crawler \
	    python crawler.py --sites sites.csv --run baseline2 \
	    --out $(DB) --workers 4 \
	    --reuse-urls-from $(DB):baseline

# blockers = ubol + pbadger (sequential, shared DB)
blockers: ubol pbadger

ubol: dirs
	$(COMPOSE_VPN) run --rm crawler \
	    python crawler.py --sites sites.csv --run ubol \
	    --out $(DB) --workers 4 \
	    --extension ext/ubol \
	    --reuse-urls-from $(DB):baseline

pbadger: dirs
	$(COMPOSE_VPN) run --rm crawler \
	    python crawler.py --sites sites.csv --run pbadger \
	    --out $(DB) --workers 4 \
	    --extension ext/privacybadger \
	    --reuse-urls-from $(DB):baseline

optout: dirs
	$(COMPOSE_VPN) run --rm crawler \
	    python crawler.py --sites sites.csv --run optout \
	    --out $(DB) --workers 4 \
	    --consent reject --gpc \
	    --reuse-urls-from $(DB):baseline

accept: dirs
	$(COMPOSE_VPN) run --rm crawler \
	    python crawler.py --sites sites.csv --run accept \
	    --out $(DB) --workers 4 \
	    --consent accept \
	    --reuse-urls-from $(DB):baseline

# Separate the two opt-out signals that `optout` combines (reject + GPC).
rejectonly: dirs
	$(COMPOSE_VPN) run --rm crawler \
	    python crawler.py --sites sites.csv --run rejectonly \
	    --out $(DB) --workers 4 \
	    --consent reject \
	    --reuse-urls-from $(DB):baseline

gpconly: dirs
	$(COMPOSE_VPN) run --rm crawler \
	    python crawler.py --sites sites.csv --run gpconly \
	    --out $(DB) --workers 4 \
	    --gpc \
	    --reuse-urls-from $(DB):baseline

# Third identical baseline: estimates the noise floor's own variability.
baseline3: dirs
	$(COMPOSE_VPN) run --rm crawler \
	    python crawler.py --sites sites.csv --run baseline3 \
	    --out $(DB) --workers 4 \
	    --reuse-urls-from $(DB):baseline

respawn: dirs
	$(COMPOSE_VPN) run --rm crawler \
	    python respawn.py --sites respawn_sites.csv --clear cookies \
	    --out $(RESPAWN_DB) --workers 2
	$(COMPOSE_VPN) run --rm crawler \
	    python respawn.py --sites respawn_sites.csv --clear all \
	    --out $(RESPAWN_DB) --workers 2

analyze: dirs
	$(COMPOSE) run --rm crawler \
	    python analyze.py $(DB) --baseline baseline --outdir $(RESULTS)

# Paired tests vs the baseline2 noise floor (after analyze).
stats: dirs
	$(COMPOSE) run --rm crawler python scripts/stats.py $(RESULTS)/comparison.csv --out $(RESULTS)/stats.csv

# Stratified sample of detections to label by hand; score with scripts/validation.py score.
validation-sample: dirs
	$(COMPOSE) run --rm crawler python scripts/validation.py sample $(RESULTS)/fp_scripts.csv \
	    --out $(RESULTS)/validation_sample.csv

# --- Helpers ---
# Headful run example (run from inside the container or on the host with X11):
#   docker compose run --rm crawler xvfb-run -a python crawler.py --sites sites.csv --run baseline --out data/crawl.sqlite --workers 1 --headful

# Verify egress IP (should be your VPN exit, not your home IP):
#   docker compose -f docker-compose.yml -f docker-compose.vpn.yml run --rm crawler \
#     python -c "import urllib.request; print(urllib.request.urlopen('https://ifconfig.me').read().decode())"
