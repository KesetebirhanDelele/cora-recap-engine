#!/usr/bin/env bash
# Weekly disk hygiene for the Hetzner Docker host.
#
# Removes ONLY regenerable Docker leftovers:
#   - build cache older than 7 days   (the 2026-11-05 audit found 44 GB of it)
#   - dangling (untagged) images
# Never touches: volumes (database), running images, the system journal, backups.
# Safe to run any time; the next `docker compose up -d --build` just rebuilds a little slower.
#
# Install on the server (once):
#   ( crontab -l 2>/dev/null; echo '0 3 * * 0 /opt/cora-recap-engine/scripts/server_disk_hygiene.sh >> /var/log/cora-disk-hygiene.log 2>&1' ) | crontab -
set -euo pipefail

echo "=== $(date -u +%FT%TZ) disk hygiene start ==="
df -h / | tail -1
docker builder prune -f --filter "until=168h"
docker image prune -f
df -h / | tail -1
echo "=== done ==="
