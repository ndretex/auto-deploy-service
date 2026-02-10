# Auto-Deploy Service

A centralized Python service that watches one or more Git repositories, pulls updates when branches fall behind, and redeploys code through Docker Compose, systemd services, or custom scripts. The same process also exposes a lightweight Flask monitoring UI (with Chart.js graphs) from `monitoring/templates` + `monitoring/static` and publishes health data that drives downtime/deploy charts.

## Project Layout

- `auto-deploy.py` – main engine + monitor runner that loads `config.yaml`, checks Git status, redeploys, and runs the Flask web UI on the configured `global.web_port`.
- `auto-deploy.service` – systemd unit that expects a Python virtualenv at `.venv` inside the repo (`ExecStart=.venv/bin/python auto-deploy.py config.yaml`).
- `auto-deploy-firewall.service` – systemd oneshot unit that enforces host firewall rules so the dashboard port only accepts loopback traffic.
- `scripts/enforce-dashboard-firewall.sh` – idempotent rule sync script used by `auto-deploy-firewall.service`.
- `install.sh` – convenience installer that installs Python system packages, makes the script executable, creates `/var/log/auto-deploy`, and registers the service.
- `config.example.yaml` – canonical configuration template.
- `config.yaml` – user configuration (copy from the example and edit per environment).
- `.env.example` – environment template for dashboard credentials.
- `deploy/nginx/auto-deploy-monitor.conf.example` + `deploy/caddy/Caddyfile.example` – TLS reverse proxy examples for secure external access.
- `requirements.txt` – dependencies (`PyYAML`, `requests`, `Flask`, `docker`).
- `monitoring/templates/index.html` + `monitoring/static/monitor.js` – served by Flask; drives the status table, refresh control, and Chart.js graphs.
- `logs/` – optional development directory for run records outside the system log.

## Features

- ✅ **Multi-project orchestration**: configure as many repositories as you need and choose docker-compose, systemd, or custom scripts per entry.
- ✅ **Smart Git detection**: the engine fetches remotes, compares merge bases, stashes local changes if needed, and redeploys only when the local branch is strictly behind.
- ✅ **Health monitoring**: optional `projects[].health` block supports HTTP probes (`url` + `expected_status`), container-centric checks, or custom scripts; results land in both the UI table and the downtime/deployment aggregates.
- ✅ **Flask + Chart.js UI**: the embedded interface polls `/api/health`, `/api/downtime`, and `/api/deployments`, renders a status table, and draws downtime/deployment bar charts without bundlers.
- ✅ **Dashboard authentication**: monitor endpoints require HTTP Basic auth credentials loaded from `.env`.
- ✅ **Runtime modes**: run the engine alone (`--mode=engine`) or run the engine + monitor (`--mode=all`, the default).
- ✅ **Notifications**: webhook payloads include both `text` and `content` (compatible with Slack and Discord) and fire when deployments succeed/fail, Git checks error, or divergence is detected.
- ✅ **History retention**: downtime and deployment charts are built from retention-configurable history buffers (`global.history_retention_days`).
- ✅ **Logging & rotation**: logs land in `global.log_directory` (default `/var/log/auto-deploy/auto-deploy.log`) with a `TimedRotatingFileHandler` that keeps `global.log_retention_days` worth of files.

## Quick Start

### Prerequisites

1. Linux host with Python 3.11+ (and systemd if you plan to install the service), Git, and Docker if you deploy Compose projects.
2. Network access to all monitored repositories, webhooks, and health probes.
3. A Python virtualenv inside the repo (`python3 -m venv .venv`) keeps dependencies isolated.

### Prepare the environment

```bash
cd /root/auto-deploy-service
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
cp .env.example .env
# edit config.yaml to describe your projects, branches, and webhook URLs
nano config.yaml
# set dashboard credentials
nano .env
```

### Try the engine manually

```bash
python3 auto-deploy.py --once
```

Add `--mode=engine` to skip the Flask UI and health scheduler when you only need the deploy loop, or `--mode=all` (default) to keep both pieces active.

### Install as a systemd service

```bash
chmod +x install.sh
sudo ./install.sh
```

`install.sh` installs `python3-yaml` + `python3-requests`, creates `/var/log/auto-deploy`, copies `auto-deploy.service` and `auto-deploy-firewall.service` into `/etc/systemd/system`, reloads systemd, and enables both services. The bundled app unit points at `.venv/bin/python`, so create that virtualenv and install `requirements.txt` before running the installer or edit the service file to use your preferred interpreter path.

## Runtime modes

`auto-deploy.py` accepts an optional `--mode=<all|engine>` flag (default `all`).

- `--mode=all`: runs the engine loop, starts the Flask UI, and kicks off the health scheduler.
- `--mode=engine`: skips the Flask/health components when you only care about the deploy engine (useful for cron jobs or containerized deployments without the UI).

## Monitoring UI

The Flask app exposes the following endpoints:

- `/` → `monitoring/templates/index.html` (status table, refresh button, downtime/deployment canvases).
- `/static/monitor.js` → polls `/api/health`, formats ISO timestamps, and feeds Chart.js.
- `/api/health` → current health status per project.
- `/api/downtime?range=5m|15m|30m|3h|12h|15d|30d|90d` → downtime percentage buckets used by the downtime chart.
- `/api/deployments?range=5m|15m|30m|3h|12h|15d|30d|90d` → deployment counts per bucket.

Chart.js is loaded from `https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js`, the client refreshes every 30 seconds, and a manual refresh button pulls the latest health data. Dashboard auth is required and is read from `.env`.

## Configuration

```yaml
global:
  check_interval: 300                  # seconds between check cycles
  web_host: 127.0.0.1                  # bind to localhost; expose via TLS proxy
  web_port: 8000                       # Flask UI port (change to suit your network)
  log_directory: /var/log/auto-deploy
  log_retention_days: 7
  history_retention_days: 7
  notifications:
    enabled: false
    webhook_url: ""

projects:
  - name: "My Project"
    path: /path/to/project
    branch: main
    deploy_method: docker-compose
    docker_compose:
      service_name: web
      build_flags: "--build"
      up_flags: "-d"
    health:
      url: http://localhost:8000/health
      expected_status: 200
      container_name: web
    pre_deploy:
      - echo "Backing up..."
    post_deploy:
      - docker system prune -f
    enabled: true
```

- `global.history_retention_days` controls how much health/deploy history is kept for the charts.
- `global.web_host` should remain `127.0.0.1` for secure deployments.
- `global.web_port` chooses the port the Flask UI listens on (default 8000).
- `projects[].health` can declare HTTP probes (`url`, `expected_status`), a `container_name` to label results, or a custom `script` when probing requires logic.
- `pre_deploy`/`post_deploy` are ordered shell commands executed in the project directory.
- `projects[].enabled` lets you disable entries without removing them.

Use `config.example.yaml` as your starting point.

### Dashboard Auth (.env)

```env
AUTO_DEPLOY_DASHBOARD_USERNAME=admin
AUTO_DEPLOY_DASHBOARD_PASSWORD_HASH=
# Optional fallback only if HASH is empty:
# AUTO_DEPLOY_DASHBOARD_PASSWORD=change-me
AUTO_DEPLOY_DASHBOARD_REALM=Auto-Deploy Monitor
```

- Use `AUTO_DEPLOY_DASHBOARD_PASSWORD_HASH` (recommended) or `AUTO_DEPLOY_DASHBOARD_PASSWORD`.
- Generate a hash with:
  - `python3 -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('change-me'))"`
- `.env` is auto-loaded from the repository root and is ignored by Git.

### TLS Reverse Proxy

- Keep the app bound to localhost (`global.web_host: 127.0.0.1`).
- Use one of the provided secure proxy examples:
  - Nginx: `deploy/nginx/auto-deploy-monitor.conf.example`
  - Caddy: `deploy/caddy/Caddyfile.example`
- Point proxy upstream to `127.0.0.1:<global.web_port>`.

### Firewall Guard

- `auto-deploy-firewall.service` runs `scripts/enforce-dashboard-firewall.sh` and inserts host firewall rules that drop non-loopback traffic to `global.web_port`.
- The rule is idempotent and is re-applied at boot (`WantedBy=multi-user.target`), so port exposure stays locked down even if host firewall defaults are permissive.
- Manual run:
  - `sudo /root/auto-deploy-service/scripts/enforce-dashboard-firewall.sh /root/auto-deploy-service/config.yaml`

## Deployment Methods

### Docker Compose

```yaml
deploy_method: docker-compose
docker_compose:
  service_name: app
  build_flags: "--build"
  up_flags: "-d"
```

The engine runs `docker compose down --remove-orphans` followed by `docker compose up <service> <up_flags> <build_flags>`.

### Systemd

```yaml
deploy_method: systemd
systemd:
  service_name: my-service
```

Restarts the named systemd service and reports health via `systemctl is-active`.

### Custom

```yaml
deploy_method: custom
custom:
  deploy_script: /path/to/deploy.sh
```

Runs your script with `run_command`; any failure cancels deployment and logs the error.

## Notifications

Enabled notifications POST both `text` and `content` to the webhook URL so that Slack, Discord, and similar services receive the same payload. Notifications are dispatched when deployments succeed/fail, git checks fail, or divergence is detected.

## Usage

### Manual Testing

```bash
python3 auto-deploy.py --once
python3 auto-deploy.py config.yaml --once --mode=engine
```

### Service Management

```bash
sudo systemctl start auto-deploy
sudo systemctl restart auto-deploy
sudo systemctl stop auto-deploy
sudo systemctl status auto-deploy
sudo systemctl status auto-deploy-firewall
sudo journalctl -u auto-deploy -f
sudo tail -f /var/log/auto-deploy/*.log
```

`auto-deploy.service` runs `.venv/bin/python` from the repo root. Adjust the unit file if your virtualenv lives elsewhere or you want to use a different user.

## How It Works

1. Every `check_interval` seconds, the engine fetches each project, compares local, remote, and merge-base commits, and if the local branch is behind it begins an update.
2. It stashes local changes (if any), pulls, and runs `pre_deploy` hooks before the configured deployment method.
3. Post-deploy hooks run afterward, `deploy_history` is recorded, and notifications fire on success/failure.
4. A background thread updates health statuses per project and feeds the monitoring UI with `history` buckets that power the charts.

## Logs

- The primary log file is at `global.log_directory` (default `/var/log/auto-deploy/auto-deploy.log`).
- The service also writes to the systemd journal (`journalctl -u auto-deploy`).
- The `logs/` directory is available for local testing outside systemd.

## Troubleshooting

```bash
cd /root/auto-deploy-service
python3 auto-deploy.py --once
sudo journalctl -u auto-deploy -n 100
sudo tail -n 200 /var/log/auto-deploy/*.log
```

- Use `git fetch origin <branch>` / `git merge-base` manually to check Git access.
- `docker compose config` verifies compose files before deployment.
- `health.script` gives you a way to run tests or curl commands inside the project before health is reported healthy.

## Security Considerations

1. Use SSH keys or credentials helpers for Git and keep webhook URLs out of committed code (`chmod 600 config.yaml`).
2. Use a strong `AUTO_DEPLOY_DASHBOARD_PASSWORD_HASH` in `.env` (preferred over plaintext password).
3. Always terminate TLS at a reverse proxy (Nginx/Caddy) before exposing dashboard access.
4. Keep `auto-deploy-firewall.service` enabled so non-loopback access to `global.web_port` is dropped at host firewall level.
5. Protect `.env`, `.venv`, and `config.yaml` files so only the deploy user (often root) can read them.

## Advanced Configuration

### Multiple check intervals

Run separate instances with different configs if you need finer control:

```bash
python3 auto-deploy.py config-fast.yaml
python3 auto-deploy.py config-slow.yaml --mode=engine
```

### Conditional deployments

```yaml
pre_deploy:
  - "if [ -f .deploy-skip ]; then exit 1; fi"
  - "npm test"
```

### Health scripts

```yaml
health:
  url: https://localhost/health
  expected_status: 200
  container_name: my-app
  script: /path/to/custom-health.sh
```

### History retention

`global.history_retention_days` controls how much data feeds `/api/downtime` and `/api/deployments`. Increase it if you want longer charts and are willing to keep a few more history points in memory.

## Comparison with Existing Tools

| Feature | Auto-Deploy Service | Watchtower | Portainer | GitHub Actions |
|---------|-------------------|------------|-----------|----------------|
| Git-based deployment | ✅ | ❌ | ❌ | ✅ |
| Image-based deployment | ❌ | ✅ | ✅ | ✅ |
| Multi-project monitoring | ✅ | ✅ | ✅ | Per repo |
| Built-in monitoring UI | ✅ | ❌ | ❌ | ❌ |
| Self-hosted | ✅ | ✅ | ✅ | ❌ |
| Lightweight | ✅ | ✅ | ❌ | N/A |
| YAML config | ✅ | ❌ | UI-only | YAML |
| Custom deployment hooks | ✅ | ❌ | Limited | ✅ |

## Uninstallation

```bash
sudo systemctl stop auto-deploy
sudo systemctl disable auto-deploy
sudo systemctl stop auto-deploy-firewall
sudo systemctl disable auto-deploy-firewall
sudo rm /etc/systemd/system/auto-deploy.service
sudo rm /etc/systemd/system/auto-deploy-firewall.service
sudo systemctl daemon-reload
rm -rf /root/auto-deploy-service
rm -rf /var/log/auto-deploy
```

## License

MIT License - Free to use and modify
