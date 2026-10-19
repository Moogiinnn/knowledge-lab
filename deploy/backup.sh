#!/usr/bin/env bash
# Nightly PostgreSQL backup (cron, installed by setup.sh as /usr/local/bin/knowledge-lab-backup).
# Keeps 14 days on the server. If /etc/default/knowledge-lab-backup sets S3_BUCKET and the AWS CLI
# is configured, each dump is also copied to s3://$S3_BUCKET/knowledge-lab/ (DEPLOY.md, step 7).
set -euo pipefail
DIR=/var/backups/knowledge-lab
mkdir -p "$DIR" && chmod 700 "$DIR"
FILE="$DIR/knowledge_lab-$(date -u +%Y%m%d-%H%M).dump"
sudo -u postgres pg_dump -Fc knowledge_lab > "$FILE"
chmod 600 "$FILE"
find "$DIR" -name 'knowledge_lab-*.dump' -mtime +14 -delete

[ -f /etc/default/knowledge-lab-backup ] && . /etc/default/knowledge-lab-backup
if [ -n "${S3_BUCKET:-}" ] && command -v aws >/dev/null; then
    aws s3 cp "$FILE" "s3://$S3_BUCKET/knowledge-lab/$(basename "$FILE")" --only-show-errors --sse AES256
fi
echo "backup: $FILE"
