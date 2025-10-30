#!/bin/bash

# Installation script for Auto-Deploy Service
set -e

echo "=========================================="
echo "Installing Auto-Deploy Service"
echo "=========================================="

SERVICE_DIR="/root/auto-deploy-service"
SERVICE_NAME="auto-deploy"

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root or with sudo"
    exit 1
fi

echo ""
echo "Step 1: Installing Python dependencies..."
cd "$SERVICE_DIR"
apt install -y python3-yaml python3-requests

echo ""
echo "Step 2: Making script executable..."
chmod +x "$SERVICE_DIR/auto-deploy.py"

echo ""
echo "Step 3: Creating log directory..."
mkdir -p /var/log/auto-deploy

echo ""
echo "Step 4: Installing systemd service..."
cp "$SERVICE_DIR/auto-deploy.service" "/etc/systemd/system/$SERVICE_NAME.service"

echo ""
echo "Step 5: Reloading systemd daemon..."
systemctl daemon-reload

echo ""
echo "Step 6: Enabling and starting service..."
systemctl enable "$SERVICE_NAME.service"
systemctl start "$SERVICE_NAME.service"

echo ""
echo "=========================================="
echo "Installation Complete!"
echo "=========================================="
echo ""
echo "Service Status:"
systemctl status "$SERVICE_NAME.service" --no-pager | head -15
echo ""
echo "Useful commands:"
echo "  - View status:       systemctl status $SERVICE_NAME"
echo "  - View logs:         journalctl -u $SERVICE_NAME -f"
echo "  - View log files:    tail -f /var/log/auto-deploy/*.log"
echo "  - Stop service:      systemctl stop $SERVICE_NAME"
echo "  - Restart service:   systemctl restart $SERVICE_NAME"
echo "  - Disable service:   systemctl disable $SERVICE_NAME"
echo "  - Edit config:       nano $SERVICE_DIR/config.yaml"
echo "  - Test once:         python3 $SERVICE_DIR/auto-deploy.py --once"
echo ""
echo "Configuration file: $SERVICE_DIR/config.yaml"
echo ""
