# Quick Start Guide - Auto-Deploy Service

## What Was Created

A centralized auto-deployment service that monitors both projects from a single configuration file.

### Files Created:
- `/root/auto-deploy-service/` - Main service directory
  - `auto-deploy.py` - Python script that monitors and deploys
  - `config.yaml` - Configuration file listing all projects
  - `auto-deploy.service` - Systemd service file
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
