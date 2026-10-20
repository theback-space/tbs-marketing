#!/usr/bin/env bash
# One-time setup and redeploy for an Ubuntu 24.04 server (e.g. AWS Lightsail).
# Run from the repository root:  bash deploy/setup.sh
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo "Installing Docker..."
  sudo apt-get update -y
  sudo apt-get install -y docker.io docker-compose-v2
  sudo systemctl enable --now docker
fi

# 1 GB plans can run out of memory while building; add swap once.
if [ ! -f /swapfile ]; then
  sudo fallocate -l 1G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo "Created .env from the example. Fill it in (nano .env), then run this script again."
  exit 1
fi

missing=()
for key in TBS_DOMAIN TBS_DASHBOARD_PASSWORD ANTHROPIC_API_KEY; do
  grep -Eq "^${key}=.+" .env || missing+=("$key")
done
if [ ${#missing[@]} -gt 0 ]; then
  echo "Set these in .env first: ${missing[*]}"
  exit 1
fi

sudo docker compose -f docker-compose.prod.yml up -d --build
sudo docker compose -f docker-compose.prod.yml ps
domain=$(grep -E '^TBS_DOMAIN=' .env | cut -d= -f2-)
echo
echo "Done. Open https://${domain} (the certificate can take a minute on first start)."
echo "Logs:   sudo docker compose -f docker-compose.prod.yml logs -f social"
