COMPOSE     = docker compose
COMPOSE_VPN = docker compose -f docker-compose.yml -f docker-compose.vpn.yml
DB          = data/crawl.sqlite
RESPAWN_DB  = data/respawn.sqlite
RESULTS     = results

.PHONY: build dirs preflight preflight-vpn smoke baseline baseline2 blockers ubol pbadger optout accept respawn analyze

dirs:
	mkdir -p data ext results

build: dirs
	$(COMPOSE) build

# --- Isolation pre-flight (run before any real crawl) ---
preflight: build
	@echo "[1] user (expect non-root pwuser):"; $(COMPOSE) run --rm crawler id
	@echo "[2] docker socket (expect absent):"; $(COMPOSE) run --rm crawler sh -c 'test ! -e /var/run/docker.sock && echo absent'
	@echo "[3] .env in image (expect absent):"; $(COMPOSE) run --rm crawler sh -c 'test ! -e /app/.env && echo absent'
	@echo "[4] Chromium sandbox without --no-sandbox:"; $(COMPOSE) run --rm crawler python -c \
	  "from playwright.sync_api import sync_playwright as s; p=s().start(); b=p.chromium.launch(channel='chromium'); print('ok', b.version); b.close(); p.stop()"
	@echo "[5] mounts (expect only data rw, ext ro, results rw):"; $(COMPOSE) config | grep -A8 'volumes:'

# Needs .env. Prints the egress IP: it must be the VPN exit, not your real IP.
# Kill-switch check is manual: `docker compose ... stop gluetun`, then confirm the crawler has no connectivity.
preflight-vpn:
	@echo "[egress IP via VPN]"; $(COMPOSE_VPN) run --rm crawler python -c "import urllib.request as u; print(u.urlopen('https://ifconfig.me',timeout=20).read().decode())"

# --- Smoke test (no VPN; fixture server inside the container) ---
smoke: dirs build
	$(COMPOSE) run --rm crawler python test/smoke.py

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

# --- Helpers ---
# Headful run example (run from inside the container or on the host with X11):
#   docker compose run --rm crawler xvfb-run -a python crawler.py --sites sites.csv --run baseline --out data/crawl.sqlite --workers 1 --headful

# Verify egress IP (should be your VPN exit, not your home IP):
#   docker compose -f docker-compose.yml -f docker-compose.vpn.yml run --rm crawler \
#     python -c "import urllib.request; print(urllib.request.urlopen('https://ifconfig.me').read().decode())"
