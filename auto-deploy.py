#!/usr/bin/env python3
"""
Auto-Deploy Service
A centralized service that monitors multiple git repositories for updates
and automatically deploys changes using configured deployment methods.
"""

import os
import re
import sys
import yaml
import subprocess
import logging
from hmac import compare_digest
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional
from urllib.parse import urlparse
import time
import requests
try:
    import docker
except Exception:
    docker = None
from threading import Thread, Lock
from flask import Flask, Response, jsonify, render_template, request
from logging.handlers import TimedRotatingFileHandler
from werkzeug.security import check_password_hash

class AutoDeployService:
    def __init__(self, config_path: str, mode: str = 'all'):
        self.config_path = config_path
        self.load_env_file()
        self.config = self.load_config()
        self.setup_logging()
        # Mode controls which components run: 'engine', 'monitor', or 'all'
        self.mode = mode
        # Health/status store and synchronization
        self.status_lock = Lock()
        self.statuses: Dict[str, Dict] = {}
        # Recent health history per project: list of (timestamp_seconds, is_unhealthy)
        self.history: Dict[str, List[tuple]] = {}
        # Deployment history per project: list of timestamp_seconds
        self.deploy_history: Dict[str, List[int]] = {}
        # retention for history in seconds (default 7 days)
        self.history_retention_seconds = int(self.config.get('global', {}).get('history_retention_days', 7)) * 24 * 3600
        # retention for deploy history (same default)
        self.deploy_history_retention_seconds = self.history_retention_seconds
        # Web server host/port (defaults bind monitor to localhost only)
        self.web_host = str(self.config.get('global', {}).get('web_host', '127.0.0.1'))
        # Web server port (optional override in config.global.web_port)
        self.web_port = int(self.config.get('global', {}).get('web_port', 8000))
        # Cached project port labels for monitoring table
        self.project_ports = self.build_project_ports_map()
        # Docker client (optional): prefer SDK, fall back to shell commands
        self.docker_client = None
        if docker is not None:
            try:
                self.docker_client = docker.from_env()
            except Exception as e:
                # Log full exception and continue using shell fallback
                try:
                    self.logger.exception('Docker SDK not available or cannot connect to Docker; falling back to shell')
                except Exception:
                    # If logger isn't yet fully configured, fallback to basic logging
                    logging.getLogger(__name__).exception('Docker SDK init failed')
                self.docker_client = None

    def send_notification(self, message: str) -> None:
        notifications = self.config.get('global', {}).get('notifications', {}) or {}
        if not notifications.get('enabled', False):
            return

        webhook_url = (notifications.get('webhook_url') or '').strip()
        if not webhook_url:
            self.logger.warning('Notifications enabled but webhook_url is empty')
            return

        try:
            # Support common webhook payload shapes (Slack uses `text`, Discord uses `content`).
            resp = requests.post(
                webhook_url,
                json={'text': message, 'content': message},
                timeout=10,
            )
            if resp.status_code >= 400:
                self.logger.error(f'Notification failed: HTTP {resp.status_code} {resp.text[:500]}')
        except Exception as e:
            self.logger.error(f'Notification failed: {e}')
        
    def load_config(self) -> Dict:
        """Load configuration from YAML file."""
        with open(self.config_path, 'r') as f:
            return yaml.safe_load(f)

    def load_env_file(self) -> None:
        """Load .env file from script directory into process env (without overriding existing vars)."""
        env_path = Path(__file__).parent / '.env'
        if not env_path.exists():
            return

        try:
            with env_path.open('r', encoding='utf-8') as env_file:
                for raw_line in env_file:
                    line = raw_line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if line.startswith('export '):
                        line = line[len('export '):].strip()
                    if '=' not in line:
                        continue

                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    if not key:
                        continue

                    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                        value = value[1:-1]

                    if key not in os.environ:
                        os.environ[key] = value
        except Exception:
            logging.getLogger(__name__).exception('Failed to load .env file')
    
    def setup_logging(self):
        """Configure logging with both file and console output."""
        log_dir = Path(self.config['global']['log_directory'])
        log_dir.mkdir(parents=True, exist_ok=True)

        # Retention in days for rotated files
        retention_days = int(self.config.get('global', {}).get('log_retention_days', 7))
        log_file = log_dir / 'auto-deploy.log'

        try:
            handler = TimedRotatingFileHandler(filename=str(log_file), when='midnight', interval=1, backupCount=retention_days)
            formatter = logging.Formatter('[%(asctime)s] %(levelname)s - %(message)s')
            handler.setFormatter(formatter)

            root_logger = logging.getLogger()
            root_logger.setLevel(logging.INFO)
            # Avoid adding duplicate file handler
            if not any(isinstance(h, TimedRotatingFileHandler) and getattr(h, 'baseFilename', '') == str(log_file) for h in root_logger.handlers):
                root_logger.addHandler(handler)
            # Ensure console output exists
            if not any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers):
                root_logger.addHandler(logging.StreamHandler(sys.stdout))

            self.logger = logging.getLogger(__name__)
            self.logger.info("="*60)
            self.logger.info("Auto-Deploy Service started")
            self.logger.info("="*60)
        except Exception:
            logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s - %(message)s')
            self.logger = logging.getLogger(__name__)
            self.logger.warning("Failed to initialize TimedRotatingFileHandler, using basic logging")


    def _port_sort_key(self, value: str):
        """Sort key for port labels so numeric values appear first."""
        match = re.search(r'\d+', str(value))
        if match:
            return (0, int(match.group(0)), str(value))
        return (1, 0, str(value))

    def _format_ports(self, ports: List[str]) -> str:
        cleaned = []
        for port in ports:
            if port is None:
                continue
            value = str(port).strip().strip('"').strip("'")
            if not value:
                continue
            cleaned.append(value)
        if not cleaned:
            return 'n/a'
        unique = sorted(set(cleaned), key=self._port_sort_key)
        return ', '.join(unique)

    def resolve_project_ports(self, project: Dict) -> str:
        """Resolve displayable port labels for a project."""
        explicit_ports = project.get('ports')
        if explicit_ports:
            if isinstance(explicit_ports, list):
                return self._format_ports(explicit_ports)
            return self._format_ports([explicit_ports])

        ports: List[str] = []
        health_cfg = project.get('health', {}) or {}
        health_url = health_cfg.get('url')
        if health_url:
            try:
                parsed = urlparse(health_url)
                if parsed.port:
                    ports.append(str(parsed.port))
            except Exception:
                pass

        if project.get('name') == 'Auto Deploy Service':
            ports.append(str(self.web_port))

        if project.get('deploy_method') == 'docker-compose':
            path = project.get('path')
            if path and os.path.isdir(path):
                success, output = self.run_command('docker compose config', cwd=path)
                if success:
                    try:
                        compose_cfg = yaml.safe_load(output) or {}
                        services = compose_cfg.get('services', {}) or {}
                        for service_cfg in services.values():
                            for port in service_cfg.get('ports', []) or []:
                                if isinstance(port, str):
                                    entry = port.strip()
                                    if '/' in entry:
                                        entry = entry.split('/', 1)[0]
                                    ports.append(entry)
                                elif isinstance(port, dict):
                                    published = port.get('published')
                                    target = port.get('target')
                                    protocol = port.get('protocol')
                                    if published is not None and target is not None:
                                        entry = f'{published}:{target}'
                                    elif published is not None:
                                        entry = str(published)
                                    elif target is not None:
                                        entry = f'->{target}'
                                    else:
                                        continue
                                    if protocol and protocol != 'tcp':
                                        entry = f'{entry}/{protocol}'
                                    ports.append(entry)
                    except Exception:
                        self.logger.exception(
                            f"Failed to parse compose ports for {project.get('name', 'unknown')}"
                        )

        return self._format_ports(ports)

    def build_project_ports_map(self) -> Dict[str, str]:
        ports_map: Dict[str, str] = {}
        for project in self.config.get('projects', []):
            name = project.get('name')
            if not name:
                continue
            ports_map[name] = self.resolve_project_ports(project)
        return ports_map

    def get_web_auth_config(self) -> Dict[str, object]:
        """Return mandatory dashboard auth configuration from environment variables."""
        username = str(os.getenv('AUTO_DEPLOY_DASHBOARD_USERNAME', 'admin') or 'admin')
        password_hash = str(os.getenv('AUTO_DEPLOY_DASHBOARD_PASSWORD_HASH', '') or '')
        password = str(os.getenv('AUTO_DEPLOY_DASHBOARD_PASSWORD', '') or '')
        realm = str(os.getenv('AUTO_DEPLOY_DASHBOARD_REALM', 'Auto-Deploy Monitor') or 'Auto-Deploy Monitor')
        is_configured = bool(password_hash or password)
        return {
            'username': username,
            'password_hash': password_hash,
            'password': password,
            'realm': realm,
            'is_configured': is_configured,
        }

    def create_flask_app(self) -> Flask:
        """Create Flask app and register routes for monitoring."""
        template_dir = Path(__file__).parent / 'monitoring' / 'templates'
        static_dir = Path(__file__).parent / 'monitoring' / 'static'
        app = Flask(__name__, template_folder=str(template_dir), static_folder=str(static_dir))
        auth_config = self.get_web_auth_config()

        if not auth_config['is_configured']:
            raise RuntimeError(
                "Dashboard password is missing. Set AUTO_DEPLOY_DASHBOARD_PASSWORD_HASH "
                "(recommended) or AUTO_DEPLOY_DASHBOARD_PASSWORD in .env."
            )

        def auth_required_response(message: str) -> Response:
            return Response(
                message,
                401,
                {'WWW-Authenticate': f'Basic realm="{auth_config["realm"]}"'},
            )

        @app.before_request
        def enforce_web_auth():
            provided = request.authorization
            if provided is None or (provided.type or '').lower() != 'basic':
                return auth_required_response('Authentication required')

            username_ok = compare_digest(provided.username or '', str(auth_config['username']))
            if auth_config['password_hash']:
                try:
                    password_ok = check_password_hash(
                        str(auth_config['password_hash']),
                        provided.password or '',
                    )
                except Exception:
                    password_ok = False
            else:
                password_ok = compare_digest(provided.password or '', str(auth_config['password']))

            if not (username_ok and password_ok):
                return auth_required_response('Invalid credentials')
            return None

        @app.route('/api/health')
        def api_health():
            with self.status_lock:
                return jsonify(self.statuses)

        @app.route('/api/downtime')
        def api_downtime():
            # Query param 'range' accepts: '7d','3d','24h','1h'
            range_key = request.args.get('range', '7d')
            now = int(time.time())

            if range_key == '7d':
                total_seconds = 7 * 24 * 3600
                bucket_size = 3600
            elif range_key == '3d':
                total_seconds = 3 * 24 * 3600
                bucket_size = 3600
            elif range_key == '24h':
                total_seconds = 24 * 3600
                bucket_size = 300
            elif range_key == '1h':
                total_seconds = 3600
                bucket_size = 60
            else:
                # default
                total_seconds = 7 * 24 * 3600
                bucket_size = 3600

            num_buckets = int(total_seconds // bucket_size)
            # build bucket boundaries: bucket i covers [start + i*bucket_size, start + (i+1)*bucket_size)
            start_ts = now - total_seconds
            labels = []
            for i in range(num_buckets):
                ts = start_ts + i * bucket_size
                labels.append(datetime.fromtimestamp(ts, timezone.utc).isoformat().replace('+00:00', 'Z'))

            projects = {}
            with self.status_lock:
                for pname in self.statuses.keys():
                    # initialize counts per bucket
                    counts = [0] * num_buckets
                    unhealthy = [0] * num_buckets
                    entries = self.history.get(pname, [])
                    for ts, is_unhealthy in entries:
                        if ts < start_ts:
                            continue
                        if ts >= now:
                            continue
                        idx = int((ts - start_ts) // bucket_size)
                        if idx < 0 or idx >= num_buckets:
                            continue
                        counts[idx] += 1
                        if is_unhealthy:
                            unhealthy[idx] += 1

                    # compute percentages
                    pct = []
                    for c, u in zip(counts, unhealthy):
                        if c == 0:
                            pct.append(0)
                        else:
                            pct.append(round((u / c) * 100, 2))

                    projects[pname] = pct

            return jsonify({'labels': labels, 'projects': projects})

        @app.route('/api/deployments')
        def api_deployments():
            range_key = request.args.get('range', '7d')
            now = int(time.time())

            if range_key == '7d':
                total_seconds = 7 * 24 * 3600
                bucket_size = 3600
            elif range_key == '3d':
                total_seconds = 3 * 24 * 3600
                bucket_size = 3600
            elif range_key == '24h':
                total_seconds = 24 * 3600
                bucket_size = 300
            elif range_key == '1h':
                total_seconds = 3600
                bucket_size = 60
            else:
                total_seconds = 7 * 24 * 3600
                bucket_size = 3600

            num_buckets = int(total_seconds // bucket_size)
            start_ts = now - total_seconds
            labels = [datetime.fromtimestamp(start_ts + i * bucket_size, timezone.utc).isoformat().replace('+00:00', 'Z') for i in range(num_buckets)]

            projects = {}
            with self.status_lock:
                for pname in self.statuses.keys():
                    counts = [0] * num_buckets
                    dlist = self.deploy_history.get(pname, [])
                    for ts in dlist:
                        if ts < start_ts or ts >= now:
                            continue
                        idx = int((ts - start_ts) // bucket_size)
                        if 0 <= idx < num_buckets:
                            counts[idx] += 1
                    projects[pname] = counts

            return jsonify({'labels': labels, 'projects': projects})

        @app.route('/')
        def index():
            return render_template('index.html')

        return app

    def run_command(self, cmd: str, cwd: str = None) -> tuple[bool, str]:
        """Execute a shell command and return success status and output."""
        try:
            # Convert string command to list for safer execution without shell=True
            # For complex commands, we still need shell interpretation, so use shlex
            import shlex
            cmd_list = shlex.split(cmd) if isinstance(cmd, str) else cmd
            result = subprocess.run(
                cmd_list,
                shell=False,
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

    def start_web(self, host: str = '127.0.0.1', port: int = 8000):
        """Start the Flask web server in a background thread."""
        try:
            self.app = self.create_flask_app()
            thread = Thread(target=lambda: self.app.run(host=host, port=port, use_reloader=False), daemon=True)
            thread.start()
            self.logger.info(f"Monitoring web server started on {host}:{port}")
        except Exception as e:
            self.logger.error(f"Failed to start web server: {e}")

    def should_monitor_project(self, project: Dict) -> bool:
        """Return True if project should appear in monitoring/status APIs."""
        return project.get('enabled', True) or project.get('monitor_only', False)

    def start_health_scheduler(self):
        """Start a background thread that periodically updates health statuses."""
        def scheduler():
            interval = int(self.config['global'].get('check_interval', 300))
            while True:
                try:
                    projects = self.config.get('projects', [])
                    for project in projects:
                        if not self.should_monitor_project(project):
                            continue
                        project_name = project['name']
                        if project_name not in self.project_ports:
                            self.project_ports[project_name] = self.resolve_project_ports(project)
                        status = self.check_project_health(project)
                        status['ports'] = self.project_ports.get(project_name, 'n/a')
                        with self.status_lock:
                            self.statuses[project_name] = status
                            # append history entry: timestamp seconds, is_unhealthy flag
                            ts = int(datetime.utcnow().timestamp())
                            is_unhealthy = (status.get('status') != 'healthy')
                            hlist = self.history.setdefault(project_name, [])
                            hlist.append((ts, 1 if is_unhealthy else 0))
                            # prune old entries beyond retention
                            cutoff = ts - self.history_retention_seconds
                            # keep entries with timestamp >= cutoff
                            while hlist and hlist[0][0] < cutoff:
                                hlist.pop(0)
                    time.sleep(interval)
                except Exception as e:
                    self.logger.error(f"Health scheduler error: {e}", exc_info=True)
                    time.sleep(interval)

        thread = Thread(target=scheduler, daemon=True)
        thread.start()
        self.logger.info("Health scheduler started")

    def check_project_health(self, project: Dict) -> Dict:
        """Perform health check for a single project and return status dict.

        Returns: {status: 'healthy'|'unhealthy'|'unknown', last_checked: ISO8601, details: str}
        """
        now = datetime.utcnow().isoformat() + 'Z'
        try:
            # If explicit HTTP health check configured
            health_cfg = project.get('health', {})
            explicit_container = health_cfg.get('container_name') if health_cfg else None
            if health_cfg and 'url' in health_cfg:
                url = health_cfg['url']
                expected = int(health_cfg.get('expected_status', 200))
                try:
                    resp = requests.get(url, timeout=10)
                    if resp.status_code == expected:
                        return {'status': 'healthy', 'last_checked': now, 'details': f'HTTP {resp.status_code}', 'container_name': explicit_container}
                    else:
                        return {'status': 'unhealthy', 'last_checked': now, 'details': f'HTTP {resp.status_code}', 'container_name': explicit_container}
                except Exception as e:
                    return {'status': 'unhealthy', 'last_checked': now, 'details': str(e), 'container_name': explicit_container}

            method = project.get('deploy_method')
            if method == 'docker-compose':
                service = project.get('docker_compose', {}).get('service_name')
                if not service:
                    return {'status': 'unknown', 'last_checked': now, 'details': 'No docker service_name configured'}
                # Check docker containers by name with multiple matching strategies:
                # candidates: exact service, project dir basename, compose-style names (underscore/hyphen variants)
                project_basename = Path(project.get('path', '')).name
                candidates = set()
                candidates.add(service)
                if project_basename:
                    candidates.add(project_basename)
                    candidates.add(f"{project_basename}_{service}_1")
                    candidates.add(f"{project_basename}-{service}-1")
                candidates.add(f"{service}_1")
                candidates.add(f"{service}-1")

                def _matches_container(names: str, image: str = "") -> bool:
                    if explicit_container:
                        return names == explicit_container
                    for cand in candidates:
                        if not cand:
                            continue
                        if names == cand or cand in names or cand in image:
                            return True
                    return False

                # Use Docker SDK if available for more reliable queries
                def _parse_started_at(started_at: str) -> Optional[str]:
                    # Docker times are RFC3339 like: 2026-01-08T10:00:00.123456Z
                    if not started_at:
                        return ''
                    s = started_at
                    if s.endswith('Z'):
                        s = s[:-1]
                    fmt = None
                    try:
                        # try microseconds
                        dt = datetime.strptime(s, '%Y-%m-%dT%H:%M:%S.%f')
                    except Exception:
                        try:
                            dt = datetime.strptime(s, '%Y-%m-%dT%H:%M:%S')
                        except Exception:
                            return ''
                    delta = datetime.utcnow() - dt
                    days = delta.days
                    secs = delta.seconds
                    hours = secs // 3600
                    mins = (secs % 3600) // 60
                    if days > 0:
                        return f'Up {days}d {hours}h'
                    if hours > 0:
                        return f'Up {hours}h {mins}m'
                    if mins > 0:
                        return f'Up {mins}m'
                    return 'Up a few seconds'

                containers = None
                if self.docker_client:
                    try:
                        containers = self.docker_client.containers.list(all=True)
                    except Exception as e:
                        # Don't fail hard; log and fall back to shell-based check
                        try:
                            self.logger.exception('Docker SDK error while listing containers; falling back to shell')
                        except Exception:
                            logging.getLogger(__name__).exception('Docker SDK list failed')
                        containers = None

                if containers is not None:
                    found = None
                    for c in containers:
                        names = c.name
                        image = ''
                        try:
                            image = ','.join(c.image.tags) if getattr(c.image, 'tags', None) else str(c.image)
                        except Exception:
                            image = str(getattr(c, 'image', ''))

                        if _matches_container(names, image):
                            found = c
                            break

                    if found:
                        names = found.name
                        state = found.attrs.get('State', {})
                        started_at = state.get('StartedAt', '')
                        health_info = state.get('Health', {})
                        health_status = health_info.get('Status') if health_info else None
                        uptime = _parse_started_at(started_at) or state.get('Status', '')
                        # Consider healthy if container is running and health (if present) is 'healthy'
                        running = state.get('Status') == 'running' or found.status == 'running'
                        is_healthy = running and (health_status in (None, 'healthy'))
                        status_str = 'healthy' if is_healthy else 'unhealthy'
                        details = f"{uptime} ({health_status or state.get('Status')})|{names}"
                        container_name = explicit_container or names
                        return {'status': status_str, 'last_checked': now, 'details': details, 'container_name': container_name}

                    # no matching container found via SDK
                    # fall through to shell-based check

                # Fallback to shell-based check if SDK unavailable or didn't find a match
                success, output = self.run_command("docker ps --format '{{.Status}}|{{.Names}}|{{.Image}}'")
                if not success:
                    return {'status': 'unhealthy', 'last_checked': now, 'details': output.strip(), 'container_name': explicit_container}

                found = None
                for line in output.splitlines():
                    parts = line.split('|', 2)
                    if len(parts) != 3:
                        continue
                    status_text, names, image = parts[0].strip(), parts[1].strip(), parts[2].strip()

                    if _matches_container(names, image):
                        found = (status_text, names)
                        break

                if found:
                    status_text, names = found
                    container_name = explicit_container or names
                    if 'Up' in status_text:
                        return {'status': 'healthy', 'last_checked': now, 'details': f"{status_text}|{names}", 'container_name': container_name}
                    else:
                        return {'status': 'unhealthy', 'last_checked': now, 'details': f"{status_text}|{names}", 'container_name': container_name}

                return {'status': 'unhealthy', 'last_checked': now, 'details': 'No matching container', 'container_name': explicit_container}

            elif method == 'systemd':
                service_name = project.get('systemd', {}).get('service_name')
                if not service_name:
                    return {'status': 'unknown', 'last_checked': now, 'details': 'No systemd.service_name configured'}
                success, output = self.run_command(f"systemctl is-active {service_name}")
                if success and output.strip() == 'active':
                    return {'status': 'healthy', 'last_checked': now, 'details': 'active', 'container_name': explicit_container}
                else:
                    return {'status': 'unhealthy', 'last_checked': now, 'details': output.strip(), 'container_name': explicit_container}

            elif method == 'custom':
                # Allow custom health script via project.health.script
                script = project.get('health', {}).get('script')
                if script:
                    success, output = self.run_command(script, cwd=project.get('path'))
                    return {'status': 'healthy' if success else 'unhealthy', 'last_checked': now, 'details': output.strip(), 'container_name': explicit_container}
                return {'status': 'unknown', 'last_checked': now, 'details': 'No custom health check configured', 'container_name': explicit_container}

            return {'status': 'unknown', 'last_checked': now, 'details': 'No health check available', 'container_name': explicit_container}
        except Exception as e:
            return {'status': 'unhealthy', 'last_checked': now, 'details': str(e), 'container_name': explicit_container}
    
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
            return {'error': output.strip() or 'Failed to fetch from remote'}
        
        # Get commit hashes
        success, local = self.run_command("git rev-parse @", cwd=path)
        if not success:
            self.logger.error(f"Failed to get local commit: {local}")
            return {'error': local.strip() or 'Failed to get local commit'}
        
        success, remote = self.run_command("git rev-parse @{u}", cwd=path)
        if not success:
            self.logger.error(f"Failed to get remote commit: {remote}")
            return {'error': remote.strip() or 'Failed to get remote commit'}
        
        success, base = self.run_command("git merge-base @ @{u}", cwd=path)
        if not success:
            self.logger.error(f"Failed to get merge base: {base}")
            return {'error': base.strip() or 'Failed to get merge base'}
        
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
    
    def deploy_project(self, project: Dict, update_info: Optional[Dict] = None) -> bool:
        """Deploy a project using configured method."""
        commit_message = (update_info or {}).get('commit_message', 'N/A')
        commit_author = (update_info or {}).get('commit_author', 'Auto-Deploy Service')
        self.logger.info(f"Deploying {project['name']}...")
        self.logger.info(f"  Latest commit: {commit_message}")
        self.logger.info(f"  Author: {commit_author}")
        
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

        # Record successful deployment
        try:
            ts = int(time.time())
            with self.status_lock:
                dlist = self.deploy_history.setdefault(project['name'], [])
                dlist.append(ts)
                cutoff = ts - self.deploy_history_retention_seconds
                while dlist and dlist[0] < cutoff:
                    dlist.pop(0)
        except Exception:
            self.logger.exception('Failed to record deploy history')

        return True
    
    def process_project(self, project: Dict):
        """Process a single project: check for updates and deploy if needed."""
        name = project['name']
        
        if not project.get('enabled', True):
            self.logger.debug(f"Skipping {name} (disabled)")
            return
        
        self.logger.info(f"Checking {name}...")
        
        # Verify project path exists
        self.logger.info(f"Checking path: {project['path']}")
        if not os.path.exists(project['path']):
            self.logger.error(f"Project path does not exist: {project['path']}")
            return
        
        # Check git status
        update_info = self.check_git_status(project)
        
        if update_info is None:
            self.logger.info(f"{name} is up to date")
            if project.get('deploy_method') == 'docker-compose':
                health = self.check_project_health(project)
                health_status = health.get('status', 'unknown')
                if health_status != 'healthy':
                    details = health.get('details', 'no details')
                    self.logger.warning(f"{name} up to date but container status is {health_status}: {details}")
                    recovery_info = {
                        'commit_message': f"Health recovery run (status={health_status})",
                        'commit_author': 'Auto-Deploy Service'
                    }
                    if self.deploy_project(project, recovery_info):
                        notif = f"✅ {name}: Container recovered and redeployed (status={health_status})"
                        self.logger.info(f"{name} container recovered after health check")
                        self.send_notification(notif)
                    else:
                        notif = f"❌ {name}: Recovery redeploy failed after health check (status={health_status})"
                        self.logger.error(f"{name} recovery deployment failed")
                        self.send_notification(notif)
            return

        if isinstance(update_info, dict) and update_info.get('error'):
            self.logger.error(f"{name}: Git check failed: {update_info['error']}")
            self.send_notification(f"❌ {name}: Git check failed: {update_info['error']}")
            return
        
        # Project is behind - update and deploy
        self.logger.info(f"{name} is behind remote (local: {update_info['local']}, remote: {update_info['remote']})")
        # Log explicit detection for update auditing
        self.logger.info(f"Update detected for {name}: remote={update_info['remote']} local={update_info['local']}")
        
        # Pull changes
        if not self.pull_changes(project):
            self.send_notification(f"❌ {name}: Failed to pull updates")
            return
        
        # Deploy
        commit_message = update_info.get('commit_message', 'N/A')
        commit_author = update_info.get('commit_author', 'Auto-Deploy Service')
        if self.deploy_project(project, update_info):
            message = f"✅ {name}: Successfully updated and deployed\n📝 {commit_message}\n👤 {commit_author}"
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
        # Start monitoring web server and health scheduler only if in monitor/all mode
        if self.mode in ('all', 'monitor'):
            try:
                self.start_web(host=self.web_host, port=self.web_port)
                self.start_health_scheduler()
            except Exception:
                self.logger.exception("Failed to start web/health components")

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
    
    # Parse args: --once and optional config path and --mode
    args = [arg for arg in sys.argv[1:] if arg != '--once']

    # Default values
    mode = 'all'
    config_path = None

    for arg in args:
        if arg.startswith('--mode='):
            mode = arg.split('=', 1)[1]
        elif arg.startswith('--mode'):
            # support --mode monitor (next arg)
            # Not handling this short form for simplicity
            pass
        elif not config_path:
            config_path = arg

    if not config_path:
        # Default to config.yaml in same directory as script
        script_dir = Path(__file__).parent
        config_path = script_dir / 'config.yaml'
    
    if not os.path.exists(config_path):
        print(f"Error: Config file not found: {config_path}")
        print(f"Usage: {sys.argv[0]} [config.yaml] [--once]")
        sys.exit(1)
    
    # Create and run service
    service = AutoDeployService(config_path, mode=mode)

    if run_once:
        service.run_once()
    else:
        service.run()

if __name__ == '__main__':
    main()
