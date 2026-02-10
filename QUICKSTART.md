# Quick Start Guide - Auto-Deploy Service

## What Was Created

A centralized auto-deployment service that monitors both projects from a single configuration file.

### Files Created:
- `/root/auto-deploy-service/` - Main service directory
  - `auto-deploy.py` - Python script that monitors and deploys
  - `config.yaml` - Configuration file listing all projects
  - `.env.example` - Environment template for dashboard credentials
  - `deploy/nginx/auto-deploy-monitor.conf.example` - Nginx TLS reverse proxy example
  - `deploy/caddy/Caddyfile.example` - Caddy TLS reverse proxy example
  - `auto-deploy.service` - Systemd service file
  - `auto-deploy-firewall.service` - Systemd firewall guard for dashboard port
  - `scripts/enforce-dashboard-firewall.sh` - Idempotent firewall rule sync script
  - `install.sh` - Installation script
  - `requirements.txt` - Python dependencies
  - `README.md` - Full documentation

## Quick Installation

```bash
# Install and start the service
cd /root/auto-deploy-service
sudo ./install.sh
```

That's it! The service will now:
- Check for updates every 5 minutes
- Automatically pull and deploy changes when detected
- Log all operations to `/var/log/auto-deploy/`

## Useful Commands

```bash
# View service status
systemctl status auto-deploy
systemctl status auto-deploy-firewall

# View live logs
journalctl -u auto-deploy -f

# View log files
tail -f /var/log/auto-deploy/*.log

# Test manually (run once without installing)
python3 /root/auto-deploy-service/auto-deploy.py --once

# Stop service
systemctl stop auto-deploy

# Restart service
systemctl restart auto-deploy
```

## Configuration

Edit `/root/auto-deploy-service/config.yaml` to:
- Change check interval
- Enable/disable projects
- Add new projects
- Configure notifications (webhooks)

Set dashboard credentials in `/root/auto-deploy-service/.env` (required for monitor UI):
```bash
cp /root/auto-deploy-service/.env.example /root/auto-deploy-service/.env
nano /root/auto-deploy-service/.env
# Optional hash generation helper:
python3 -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('change-me'))"
```

Keep monitor binding local-only in `config.yaml`:
```yaml
global:
  web_host: 127.0.0.1
  web_port: 8080
```

Make sure firewall guard is active (blocks non-loopback access to dashboard port):
```bash
systemctl enable --now auto-deploy-firewall
systemctl status auto-deploy-firewall
```

Expose it securely through TLS proxy:
- Nginx example: `/root/auto-deploy-service/deploy/nginx/auto-deploy-monitor.conf.example`
- Caddy example: `/root/auto-deploy-service/deploy/caddy/Caddyfile.example`

After editing config, restart the service:
```bash
systemctl restart auto-deploy
```

## How It Works

1. Service runs every 5 minutes (configurable)
2. For each project in config:
   - Fetches latest changes from git
   - Compares local vs remote commits
   - If behind: pulls changes and redeploys
   - Logs everything
3. Supports Docker Compose, systemd, and custom deployment methods

## Verification

The service was tested and successfully deployed SkyJojo when it detected updates.

Check the README at `/root/auto-deploy-service/README.md` for complete documentation.
