#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_PATH="${1:-$SERVICE_DIR/config.yaml}"
RULE_COMMENT="auto-deploy-dashboard-local-only"

if [[ ! -f "$CONFIG_PATH" ]]; then
    echo "Config file not found: $CONFIG_PATH" >&2
    exit 1
fi

WEB_PORT="$(python3 - "$CONFIG_PATH" <<'PY'
import sys
import yaml

config_path = sys.argv[1]
with open(config_path, "r", encoding="utf-8") as file_obj:
    config = yaml.safe_load(file_obj) or {}

global_config = config.get("global") or {}
web_port = int(global_config.get("web_port", 8000))
if web_port < 1 or web_port > 65535:
    raise SystemExit("Invalid global.web_port in config")

print(web_port)
PY
)"

sync_rule() {
    local tool="$1"
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "$tool not found; skipping"
        return 0
    fi

    local rule=(
        -p tcp
        --dport "$WEB_PORT"
        ! -i lo
        -m comment
        --comment "$RULE_COMMENT"
        -j DROP
    )

    if "$tool" -C INPUT "${rule[@]}" 2>/dev/null; then
        echo "$tool already protects tcp/$WEB_PORT from non-loopback traffic"
        return 0
    fi

    "$tool" -I INPUT "${rule[@]}"
    echo "$tool rule added: block non-loopback traffic to tcp/$WEB_PORT"
}

sync_rule iptables
sync_rule ip6tables
