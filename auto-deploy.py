#!/usr/bin/env python3
"""
Auto-Deploy Service
A centralized service that monitors multiple git repositories for updates
and automatically deploys changes using configured deployment methods.
"""

import os
import sys
import yaml
import subprocess
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
import time
import requests

class AutoDeployService:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = self.load_config()
        self.setup_logging()
        
    def load_config(self) -> Dict:
        """Load configuration from YAML file."""
        with open(self.config_path, 'r') as f:
            return yaml.safe_load(f)
    
    def setup_logging(self):
        """Configure logging with both file and console output."""
        log_dir = Path(self.config['global']['log_directory'])
        log_dir.mkdir(parents=True, exist_ok=True)
        
        log_file = log_dir / f"auto-deploy-{datetime.now().strftime('%Y%m%d')}.log"
        
        # Configure logging
        logging.basicConfig(
            level=logging.INFO,
            format='[%(asctime)s] %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger(__name__)
        self.logger.info("="*60)
        self.logger.info("Auto-Deploy Service started")
        self.logger.info("="*60)
    
    def run_command(self, cmd: str, cwd: str = None) -> tuple[bool, str]:
        """Execute a shell command and return success status and output."""
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )
            return result.returncode == 0, result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            return False, "Command timed out after 5 minutes"
        except Exception as e:
            return False, str(e)
    
    def send_notification(self, message: str):
        """Send notification via webhook if enabled."""
        if not self.config['global']['notifications']['enabled']:
            return
        
        webhook_url = self.config['global']['notifications']['webhook_url']
        if not webhook_url:
            return
        
        try:
            payload = {"text": message}
            requests.post(webhook_url, json=payload, timeout=10)
        except Exception as e:
            self.logger.warning(f"Failed to send notification: {e}")
    
    def check_git_status(self, project: Dict) -> Optional[Dict]:
        """
        Check if local repository is behind remote.
        Returns None if up-to-date, or dict with update info if behind.
        """
        path = project['path']
        branch = project['branch']
        
        # Fetch latest changes
        success, output = self.run_command(f"git fetch origin {branch}", cwd=path)
        if not success:
            self.logger.error(f"Failed to fetch from remote: {output}")
            return None
        
        # Get commit hashes
        success, local = self.run_command("git rev-parse @", cwd=path)
        if not success:
            self.logger.error(f"Failed to get local commit: {local}")
            return None
        
        success, remote = self.run_command("git rev-parse @{u}", cwd=path)
        if not success:
            self.logger.error(f"Failed to get remote commit: {remote}")
            return None
        
        success, base = self.run_command("git merge-base @ @{u}", cwd=path)
        if not success:
            self.logger.error(f"Failed to get merge base: {base}")
            return None
        
        local = local.strip()
        remote = remote.strip()
        base = base.strip()
        
        if local == remote:
            return None  # Up to date
        elif local == base:
            # Local is behind remote - get commit info
            success, commit_msg = self.run_command("git log -1 --pretty=%B FETCH_HEAD", cwd=path)
            success2, commit_author = self.run_command("git log -1 --pretty=%an FETCH_HEAD", cwd=path)
            
            return {
                'local': local[:8],
                'remote': remote[:8],
                'commit_message': commit_msg.strip() if success else "Unknown",
                'commit_author': commit_author.strip() if success2 else "Unknown"
            }
        elif remote == base:
            self.logger.warning(f"{project['name']}: Local is ahead of remote")
            return None
        else:
            self.logger.error(f"{project['name']}: Branches have diverged - manual intervention required")
            self.send_notification(f"⚠️ {project['name']}: Branches have diverged - manual intervention needed")
            return None
    
    def pull_changes(self, project: Dict) -> bool:
        """Pull latest changes from remote repository."""
        path = project['path']
        branch = project['branch']
        
        # Check for uncommitted changes
        success, output = self.run_command("git diff-index --quiet HEAD --", cwd=path)
        if not success:
            self.logger.warning(f"Uncommitted changes detected, stashing...")
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            self.run_command(f"git stash save 'Auto-stash before update {timestamp}'", cwd=path)
        
        # Pull changes
        success, output = self.run_command(f"git pull origin {branch}", cwd=path)
        if not success:
            self.logger.error(f"Failed to pull changes: {output}")
            return False
        
        self.logger.info(f"Successfully pulled changes")
        return True
    
    def deploy_docker_compose(self, project: Dict) -> bool:
        """Deploy using docker-compose."""
        path = project['path']
        config = project['docker_compose']
        service = config['service_name']
        build_flags = config.get('build_flags', '')
        up_flags = config.get('up_flags', '')
        
        # Stop containers
        self.logger.info("Stopping containers...")
        success, output = self.run_command("docker compose down --remove-orphans", cwd=path)
        if success:
            self.logger.info("Containers stopped successfully")
        else:
            self.logger.warning(f"Issue stopping containers (may not have been running): {output}")
        
        # Build and start containers
        self.logger.info("Building and starting containers...")
        cmd = f"docker compose up {service} {up_flags} {build_flags}"
        success, output = self.run_command(cmd, cwd=path)
        
        if not success:
            self.logger.error(f"Failed to deploy: {output}")
            return False
        
        self.logger.info("Deployment successful")
        return True
    
    def deploy_systemd(self, project: Dict) -> bool:
        """Deploy using systemd service restart."""
        service_name = project['systemd']['service_name']
        
        self.logger.info(f"Restarting systemd service: {service_name}")
        success, output = self.run_command(f"systemctl restart {service_name}")
        
        if not success:
            self.logger.error(f"Failed to restart service: {output}")
            return False
        
        self.logger.info("Service restarted successfully")
        return True
    
    def deploy_custom(self, project: Dict) -> bool:
        """Deploy using custom script."""
        script = project['custom']['deploy_script']
        
        self.logger.info(f"Running custom deployment script: {script}")
        success, output = self.run_command(script, cwd=project['path'])
        
        if not success:
            self.logger.error(f"Custom deployment failed: {output}")
            return False
        
        self.logger.info("Custom deployment successful")
        return True
    
    def run_commands(self, commands: List[str], cwd: str, phase: str):
        """Run a list of commands."""
        if not commands:
            return True
        
        self.logger.info(f"Running {phase} commands...")
        for cmd in commands:
            self.logger.info(f"  Executing: {cmd}")
            success, output = self.run_command(cmd, cwd=cwd)
            if not success:
                self.logger.error(f"  Command failed: {output}")
                return False
        return True
    
    def deploy_project(self, project: Dict, update_info: Dict) -> bool:
        """Deploy a project using configured method."""
        self.logger.info(f"Deploying {project['name']}...")
        self.logger.info(f"  Latest commit: {update_info['commit_message']}")
        self.logger.info(f"  Author: {update_info['commit_author']}")
        
        # Run pre-deployment commands
        if not self.run_commands(project.get('pre_deploy', []), project['path'], "pre-deployment"):
            return False
        
        # Deploy using configured method
        method = project['deploy_method']
        success = False
        
        if method == 'docker-compose':
            success = self.deploy_docker_compose(project)
        elif method == 'systemd':
            success = self.deploy_systemd(project)
        elif method == 'custom':
            success = self.deploy_custom(project)
        else:
            self.logger.error(f"Unknown deployment method: {method}")
            return False
        
        if not success:
            return False
        
        # Run post-deployment commands
        if not self.run_commands(project.get('post_deploy', []), project['path'], "post-deployment"):
            self.logger.warning("Post-deployment commands failed, but deployment was successful")
        
        return True
    
    def process_project(self, project: Dict):
        """Process a single project: check for updates and deploy if needed."""
        name = project['name']
        
        if not project.get('enabled', True):
            self.logger.debug(f"Skipping {name} (disabled)")
            return
        
        self.logger.info(f"Checking {name}...")
        
        # Verify project path exists
        if not os.path.exists(project['path']):
            self.logger.error(f"Project path does not exist: {project['path']}")
            return
        
        # Check git status
        update_info = self.check_git_status(project)
        
        if update_info is None:
            self.logger.info(f"{name} is up to date")
            return
        
        # Project is behind - update and deploy
        self.logger.info(f"{name} is behind remote (local: {update_info['local']}, remote: {update_info['remote']})")
        
        # Pull changes
        if not self.pull_changes(project):
            self.send_notification(f"❌ {name}: Failed to pull updates")
            return
        
        # Deploy
        if self.deploy_project(project, update_info):
            message = f"✅ {name}: Successfully updated and deployed\n📝 {update_info['commit_message']}\n👤 {update_info['commit_author']}"
            self.logger.info(f"✅ {name} successfully updated and deployed")
            self.send_notification(message)
        else:
            message = f"❌ {name}: Deployment failed after update"
            self.logger.error(message)
            self.send_notification(message)
    
    def run_once(self):
        """Run one check cycle for all projects."""
        self.logger.info("Starting update check cycle...")
        
        projects = self.config.get('projects', [])
        if not projects:
            self.logger.warning("No projects configured")
            return
        
        for project in projects:
            try:
                self.logger.info("-" * 60)
                self.process_project(project)
            except Exception as e:
                self.logger.error(f"Error processing {project.get('name', 'unknown')}: {e}", exc_info=True)
        
        self.logger.info("-" * 60)
        self.logger.info("Update check cycle completed")
    
    def run(self):
        """Run the service in continuous mode."""
        interval = self.config['global']['check_interval']
        self.logger.info(f"Running in continuous mode (check interval: {interval}s)")
        
        while True:
            try:
                self.run_once()
                self.logger.info(f"Waiting {interval} seconds until next check...")
                time.sleep(interval)
            except KeyboardInterrupt:
                self.logger.info("Received interrupt signal, shutting down...")
                break
            except Exception as e:
                self.logger.error(f"Unexpected error: {e}", exc_info=True)
                self.logger.info(f"Waiting {interval} seconds before retry...")
                time.sleep(interval)

def main():
    """Main entry point."""
    # Check for run mode
    run_once = '--once' in sys.argv
    
    # Get config file path (filter out --once flag)
    args = [arg for arg in sys.argv[1:] if arg != '--once']
    
    if len(args) > 0:
        config_path = args[0]
    else:
        # Default to config.yaml in same directory as script
        script_dir = Path(__file__).parent
        config_path = script_dir / 'config.yaml'
    
    if not os.path.exists(config_path):
        print(f"Error: Config file not found: {config_path}")
        print(f"Usage: {sys.argv[0]} [config.yaml] [--once]")
        sys.exit(1)
    
    # Create and run service
    service = AutoDeployService(config_path)
    
    if run_once:
        service.run_once()
    else:
        service.run()

if __name__ == '__main__':
    main()
