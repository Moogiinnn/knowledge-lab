#!/usr/bin/env bash
# Deploy the latest code from GitHub on the server. Run as root:
#   sudo bash /srv/knowledge-lab/deploy/update.sh
set -euo pipefail
APP_USER=klab
APP_DIR=/srv/knowledge-lab

/usr/local/bin/knowledge-lab-backup   # a fresh backup before every update
# root pulls with the read-only deploy key (DEPLOY.md, step 2); the code stays owned by klab.
git -c safe.directory="$APP_DIR" -C "$APP_DIR" pull --ff-only
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
sudo -u "$APP_USER" bash -c "cd $APP_DIR && .venv/bin/pip install -q -r requirements-server.txt \
    && .venv/bin/python manage.py migrate --noinput \
    && .venv/bin/python manage.py collectstatic --noinput -v 0"
systemctl restart knowledge-lab-web knowledge-lab-grader
echo "updated to $(git -c safe.directory="$APP_DIR" -C "$APP_DIR" log -1 --format='%h %s')"
