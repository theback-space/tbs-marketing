# First-boot setup for the tbs-social host (Amazon Linux 2023, arm64). Runs as root from user data.
set -euo pipefail
exec > >(tee -a /var/log/tbs-bootstrap.log) 2>&1

dnf install -y docker unzip
systemctl enable --now docker

# Docker Compose plugin, pinned and checksum-verified
mkdir -p /usr/local/lib/docker/cli-plugins
curl -fsSL -o /tmp/docker-compose \
  "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-aarch64"
echo "${COMPOSE_SHA256}  /tmp/docker-compose" | sha256sum -c -
install -m 755 /tmp/docker-compose /usr/local/lib/docker/cli-plugins/docker-compose

# 1 GB of swap so image builds don't run out of memory on a small instance
if [ ! -f /swapfile ]; then
  dd if=/dev/zero of=/swapfile bs=1M count=1024 status=none
  chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# Persistent data volume (database + TLS certificates). Wait for the attachment.
dev="/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_${TBS_DATA_VOLUME_ID//-/}"
for _ in $(seq 1 60); do [ -e "$dev" ] && break; sleep 5; done
[ -e "$dev" ] || { echo "data volume ${TBS_DATA_VOLUME_ID} never attached"; exit 1; }
blkid "$dev" >/dev/null 2>&1 || mkfs.xfs -q "$dev"   # format only if empty
uuid=$(blkid -s UUID -o value "$dev")
mkdir -p /data
grep -q "$uuid" /etc/fstab || echo "UUID=$uuid /data xfs defaults,nofail 0 2" >> /etc/fstab
mount -a
mkdir -p /data/app /data/caddy

cat > /etc/tbs-social.conf <<EOF
TBS_REGION=${TBS_REGION}
TBS_SECRET_ARN=${TBS_SECRET_ARN}
TBS_LOG_GROUP=${TBS_LOG_GROUP}
EOF
chmod 600 /etc/tbs-social.conf

# tbs-deploy: fetch the release named in SSM, unpack it, run its deploy script
cat > /usr/local/bin/tbs-deploy <<'EOF'
#!/bin/bash
set -euo pipefail
set -a; source /etc/tbs-social.conf; set +a
url=$(aws ssm get-parameter --region "$TBS_REGION" --name /tbs-social/release \
  --query Parameter.Value --output text)
release=$(basename "$url" .zip)
dir="/opt/tbs-social/releases/$release"
if [ ! -d "$dir" ]; then
  tmp=$(mktemp -d)
  aws s3 cp --region "$TBS_REGION" --only-show-errors "$url" "$tmp/src.zip"
  mkdir -p "$dir" && unzip -q "$tmp/src.zip" -d "$dir"
  rm -rf "$tmp"
fi
ln -sfn "$dir" /opt/tbs-social/current
export TBS_RELEASE="$release"
exec bash /opt/tbs-social/current/infra/instance/deploy.sh
EOF
chmod 755 /usr/local/bin/tbs-deploy

/usr/local/bin/tbs-deploy
