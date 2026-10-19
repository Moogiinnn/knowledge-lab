#!/usr/bin/env bash
# One-time setup of Knowledge Lab on Ubuntu 24.04 (AWS Lightsail). See deploy/DEPLOY.md.
# The repository must already be cloned to /srv/knowledge-lab. Run as root:
#   sudo HOST=kl.example.mn bash /srv/knowledge-lab/deploy/setup.sh
# HOST is the domain name (or, for a first test only, the static IP address).
set -euo pipefail

APP_USER=klab
APP_DIR=/srv/knowledge-lab
HOST=${HOST:?set HOST to the domain name or static IP, e.g. sudo HOST=kl.example.mn bash deploy/setup.sh}

[ "$(id -u)" = 0 ] || { echo "run as root (sudo)"; exit 1; }
[ -f "$APP_DIR/manage.py" ] || { echo "clone the repository to $APP_DIR first (DEPLOY.md, step 2)"; exit 1; }

echo "== packages"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get -y upgrade
DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv python3-pip git nginx curl ca-certificates \
    gnupg openssl docker.io certbot python3-certbot-nginx
# PostgreSQL 17, the same major version as development, from the PostgreSQL project's repository.
if ! dpkg -s postgresql-17 >/dev/null 2>&1; then
    install -d /usr/share/postgresql-common/pgdg
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt noble-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y postgresql-17
fi
systemctl enable --now postgresql docker nginx

echo "== application user"
id "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash "$APP_USER"
usermod -aG docker "$APP_USER"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "== database and .env"
if [ ! -f "$APP_DIR/.env" ]; then
    if sudo -u postgres psql -tAc "select 1 from pg_roles where rolname='lab'" | grep -q 1; then
        echo "the database exists but .env is missing: restore .env from your backup (it holds PSEUDONYM_KEY)"; exit 1
    fi
    DB_PASS=$(openssl rand -hex 16)
    sudo -u postgres psql -v ON_ERROR_STOP=1 -c "create role lab login password '$DB_PASS'"
    sudo -u postgres createdb -O lab knowledge_lab
    cat > "$APP_DIR/.env" <<EOF
DJANGO_SECRET_KEY=$(openssl rand -hex 32)
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=$HOST
DJANGO_HTTPS=false
DJANGO_CSRF_TRUSTED_ORIGINS=https://$HOST,http://$HOST
DATABASE_URL=postgres://lab:$DB_PASS@127.0.0.1:5432/knowledge_lab
# Back this key up outside the server (DEPLOY.md, step 8). Changing it changes every pseudonym.
PSEUDONYM_KEY=$(openssl rand -hex 32)
EOF
    chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    echo "created $APP_DIR/.env"
else
    echo ".env exists, left unchanged"
fi

echo "== Python environment and Django"
sudo -u "$APP_USER" bash -c "cd $APP_DIR && python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip \
    && .venv/bin/pip install -q -r requirements-server.txt"
sudo -u "$APP_USER" bash -c "cd $APP_DIR && .venv/bin/python manage.py migrate --noinput \
    && .venv/bin/python manage.py collectstatic --noinput -v 0 && .venv/bin/python manage.py setup_roles"

echo "== code sandbox images (Python, C/C++)"
sudo -u "$APP_USER" -g docker bash -c "cd $APP_DIR && .venv/bin/python manage.py check_code_runner --build-c"

echo "== services"
install -m 644 "$APP_DIR/deploy/knowledge-lab-web.service" "$APP_DIR/deploy/knowledge-lab-grader.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now knowledge-lab-web knowledge-lab-grader

sed "s/__HOST__/$HOST/" "$APP_DIR/deploy/nginx.conf" > /etc/nginx/sites-available/knowledge-lab
ln -sf /etc/nginx/sites-available/knowledge-lab /etc/nginx/sites-enabled/knowledge-lab
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo "== nightly database backup"
install -m 755 "$APP_DIR/deploy/backup.sh" /usr/local/bin/knowledge-lab-backup
echo "30 18 * * * root /usr/local/bin/knowledge-lab-backup" > /etc/cron.d/knowledge-lab-backup  # 02:30 Ulaanbaatar

cat <<EOF

Done. Next (DEPLOY.md):
  1. create the admin/teacher account:
       sudo -u $APP_USER bash -c "cd $APP_DIR && .venv/bin/python manage.py createsuperuser"
  2. open http://$HOST and log in (demo/test data only until HTTPS is on)
  3. with a domain name: sudo certbot --nginx -d $HOST, then set DJANGO_HTTPS=true in .env
     and run: sudo systemctl restart knowledge-lab-web
  4. copy PSEUDONYM_KEY from $APP_DIR/.env to a safe place off the server
EOF
