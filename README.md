# Auto-Deploy Service

A centralized service that monitors multiple Git repositories for updates and automatically deploys changes.

## Features

- ✅ **Multi-Project Support**: Monitor and deploy multiple projects from a single service
- ✅ **Flexible Deployment**: Supports Docker Compose, systemd, and custom deployment methods
- ✅ **Git-Based Updates**: Automatically detects when local branch is behind remote
- ✅ **Smart Deployment**: Only deploys when changes are detected
- ✅ **Logging**: Centralized logging with timestamps and rotation
- ✅ **Notifications**: Optional webhook notifications (Slack, Discord, etc.)
- ✅ **Safe Updates**: Stashes uncommitted changes before pulling
- ✅ **Pre/Post Hooks**: Run custom commands before and after deployment

## Quick Start

### Installation

1. **Install dependencies**
   ```bash
   cd /root/auto-deploy-service
   pip3 install -r requirements.txt
   ```

2. **Configure projects** (edit `config.yaml`)
   ```bash
   nano config.yaml
   ```

3. **Test manually first**
   ```bash
   python3 auto-deploy.py --once
   ```

4. **Install as systemd service**
   ```bash
   chmod +x install.sh
   sudo ./install.sh
   ```

## Configuration

Edit `config.yaml` to configure your projects:

```yaml
global:
  check_interval: 300  # Check every 5 minutes
  log_directory: /var/log/auto-deploy
  notifications:
    enabled: false
    webhook_url: ""  # Optional: Slack/Discord webhook

projects:
  - name: "My Project"
    path: /path/to/project
    branch: main
    deploy_method: docker-compose  # or systemd, custom
    
    docker_compose:
      service_name: app
      build_flags: "--build"
      up_flags: "-d"
    
    pre_deploy: []   # Commands to run before deployment
    post_deploy:     # Commands to run after deployment
      - docker system prune -f
    
    enabled: true
```

### Deployment Methods

#### Docker Compose (recommended for containerized apps)
```yaml
deploy_method: docker-compose
docker_compose:
  service_name: app
  build_flags: "--build"
  up_flags: "-d"
```

#### Systemd (for system services)
```yaml
deploy_method: systemd
systemd:
  service_name: my-service
```

#### Custom Script
```yaml
deploy_method: custom
custom:
  deploy_script: /path/to/deploy.sh
```

### Adding Notifications

Configure webhook notifications in `config.yaml`:

```yaml
global:
  notifications:
    enabled: true
    webhook_url: "https://hooks.slack.com/services/YOUR/WEBHOOK/URL"
```

**Slack webhook example:**
1. Create incoming webhook in Slack
2. Copy webhook URL
3. Add to config.yaml

**Discord webhook example:**
1. Server Settings → Integrations → Webhooks
2. Create webhook and copy URL
3. Add `/slack` to the end of Discord webhook URL
4. Add to config.yaml

## Usage

### Manual Testing
```bash
# Run once and exit
python3 auto-deploy.py --once

# Run with custom config file
python3 auto-deploy.py /path/to/config.yaml --once
```

### Service Management
```bash
# Start service
sudo systemctl start auto-deploy

# Stop service
sudo systemctl stop auto-deploy

# Restart service
sudo systemctl restart auto-deploy

# Check status
sudo systemctl status auto-deploy

# View logs (live)
sudo journalctl -u auto-deploy -f

# View log files
tail -f /var/log/auto-deploy/*.log
```

### Configuration Changes

After editing `config.yaml`:
```bash
# Restart service to apply changes
sudo systemctl restart auto-deploy
```

## How It Works

1. **Check Cycle**: Every X seconds (configured in `check_interval`)
2. **For Each Project**:
   - Fetch latest changes from remote repository
   - Compare local and remote commits
   - If local is behind:
     - Stash uncommitted changes (if any)
     - Pull latest changes
     - Run pre-deployment commands
     - Deploy using configured method
     - Run post-deployment commands
     - Send notification (if enabled)
   - If up-to-date: Skip deployment
   - If diverged: Log warning and send notification

## Logs

Logs are stored in two places:

1. **Systemd journal**: `journalctl -u auto-deploy -f`
2. **Log files**: `/var/log/auto-deploy/auto-deploy-YYYYMMDD.log`

Log format:
```
[2025-10-30 12:00:00] INFO - Starting update check cycle...
[2025-10-30 12:00:01] INFO - Checking Formation Skydiving Builder...
[2025-10-30 12:00:02] INFO - Formation Skydiving Builder is behind remote
[2025-10-30 12:00:05] INFO - Successfully pulled changes
[2025-10-30 12:00:10] INFO - ✅ Formation Skydiving Builder successfully updated
```

## Troubleshooting

### Service won't start
```bash
# Check service status and errors
sudo systemctl status auto-deploy
sudo journalctl -u auto-deploy -n 50

# Check Python and dependencies
python3 --version
pip3 list | grep -E "PyYAML|requests"
```

### Updates not detected
```bash
# Test git access manually
cd /root/formation_skydiving_builder
git fetch origin main
git status

# Check if branch is properly tracking remote
git branch -vv
```

### Deployment fails
```bash
# Check Docker status
sudo systemctl status docker
docker ps -a

# Verify docker-compose.yml
cd /root/formation_skydiving_builder
docker compose config

# Test deployment manually
docker compose down
docker compose up app -d --build
```

### Permission issues
```bash
# Ensure service runs as root (required for Docker)
sudo systemctl edit auto-deploy

# Add or verify:
[Service]
User=root
```

## Security Considerations

1. **Git Authentication**: 
   - Use SSH keys for private repositories
   - Configure git credentials to avoid password prompts
   ```bash
   git config --global credential.helper store
   ```

2. **File Permissions**:
   ```bash
   chmod 600 /root/auto-deploy-service/config.yaml
   chmod 700 /root/auto-deploy-service/auto-deploy.py
   ```

3. **Webhook Security**:
   - Keep webhook URLs secret
   - Use environment variables for sensitive data
   - Consider using encrypted configuration

## Advanced Configuration

### Different Check Intervals per Project

Currently all projects use the global `check_interval`. To have different intervals, run multiple instances with different configs:

```bash
# Instance 1: Fast updates (1 minute)
python3 auto-deploy.py config-fast.yaml

# Instance 2: Slow updates (1 hour)
python3 auto-deploy.py config-slow.yaml
```

### Conditional Deployment

Add logic to pre_deploy commands:

```yaml
pre_deploy:
  - "if [ -f .deploy-skip ]; then exit 1; fi"  # Skip if .deploy-skip exists
  - "npm test"  # Only deploy if tests pass
```

### Health Checks

Add post-deployment health checks:

```yaml
post_deploy:
  - "sleep 10"  # Wait for service to start
  - "curl -f http://localhost:3000/ || systemctl restart my-service"
```

## Comparison with Existing Tools

| Feature | Auto-Deploy Service | Watchtower | Portainer | GitHub Actions |
|---------|-------------------|------------|-----------|----------------|
| Git-based deployment | ✅ | ❌ | ❌ | ✅ |
| Image-based deployment | ❌ | ✅ | ✅ | ✅ |
| Multi-project | ✅ | ✅ | ✅ | Per repo |
| Self-hosted | ✅ | ✅ | ✅ | ❌ |
| Lightweight | ✅ | ✅ | ❌ | N/A |
| Configuration file | ✅ | ❌ | Via UI | Via YAML |
| Custom deployment | ✅ | ❌ | Limited | ✅ |

## Uninstallation

```bash
# Stop and disable service
sudo systemctl stop auto-deploy
sudo systemctl disable auto-deploy

# Remove service file
sudo rm /etc/systemd/system/auto-deploy.service
sudo systemctl daemon-reload

# Remove service directory (optional)
rm -rf /root/auto-deploy-service

# Remove logs (optional)
rm -rf /var/log/auto-deploy
```

## License

MIT License - Free to use and modify
