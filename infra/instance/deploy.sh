#!/bin/bash
# Build and (re)start the app from the current release. Run by tbs-deploy as root.
set -euo pipefail
cd /opt/tbs-social/current

# .env = non-secret config (SSM) + secrets (Secrets Manager). Platforms whose credentials
# aren't filled in yet are moved to manual mode so the app still starts.
python3 - > .env.new <<'PY'
import json, os, subprocess, sys

def aws(*args):
    return subprocess.check_output(
        ["aws", *args, "--region", os.environ["TBS_REGION"], "--output", "text"]).decode().strip()

config = json.loads(aws("ssm", "get-parameter", "--name", "/tbs-social/config", "--query", "Parameter.Value"))
secrets = json.loads(aws("secretsmanager", "get-secret-value", "--secret-id", os.environ["TBS_SECRET_ARN"],
                         "--query", "SecretString"))
env = {**config, **{k: v for k, v in secrets.items() if v}}

needs = {
    "facebook": ["META_PAGE_ID", "META_PAGE_ACCESS_TOKEN"],
    "instagram": ["META_IG_USER_ID", "META_PAGE_ACCESS_TOKEN"],
    "google": ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN",
               "GBP_ACCOUNT_ID", "GBP_LOCATION_ID"],
}
wanted = [p.strip() for p in env.get("TBS_PLATFORMS", "").split(",") if p.strip()]
manual = [p.strip() for p in env.get("TBS_MANUAL_PLATFORMS", "").split(",") if p.strip()]
ready = [p for p in wanted if all(env.get(k) for k in needs.get(p, []))]
for p in wanted:
    if p not in ready:
        print(f"# {p}: credentials missing in Secrets Manager; using manual mode for now", file=sys.stderr)
        manual.append(p)
env["TBS_PLATFORMS"] = ",".join(ready)
env["TBS_MANUAL_PLATFORMS"] = ",".join(dict.fromkeys(m for m in manual if m not in ready))

for k, v in env.items():
    if "\n" in v:
        raise SystemExit(f"{k} contains a newline; fix it in Secrets Manager")
    print(f"{k}={v}")
PY
install -m 600 .env.new .env && rm -f .env.new

if ! grep -q '^ANTHROPIC_API_KEY=.' .env; then
  echo "ANTHROPIC_API_KEY is empty in Secrets Manager (tbs-social/env). Fill it in, then redeploy."
  exit 1
fi

docker build -q -t "tbs-social:${TBS_RELEASE}" .
compose=(docker compose -f docker-compose.prod.yml -f infra/instance/docker-compose.aws.yml)
"${compose[@]}" up -d --no-build --remove-orphans

sleep 10
running=$("${compose[@]}" ps --status running --services | sort | tr '\n' ' ')
echo "running: ${running}"
[ "$running" = "caddy social " ] || { "${compose[@]}" logs --tail 50 social; exit 1; }

# keep the three most recent releases and images
ls -1dt /opt/tbs-social/releases/* | tail -n +4 | xargs -r rm -rf
docker image prune -af --filter "until=72h" >/dev/null
echo "release ${TBS_RELEASE} is live"
