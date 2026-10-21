# syntax=docker/dockerfile:1
# Not distroless: ansible, git-askpass.sh and the scripts below all need /bin/sh.
FROM python:3.12-slim-trixie

# uid 65532 is what docker-compose.yml gives the /run/hoisty tmpfs
RUN apt-get update \
 && apt-get install -y --no-install-recommends git openssh-client openssl \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd -g 65532 nonroot \
 && useradd -u 65532 -g nonroot -d /var/lib/hoisty -s /usr/sbin/nologin -M nonroot

# same layout as the .deb: app/ and venv/ root's, data/ backups/ projects/ the service account's
WORKDIR /var/lib/hoisty
COPY requirements.txt app/
RUN python3 -m venv venv \
 && venv/bin/pip install --no-cache-dir --require-hashes -r app/requirements.txt
COPY manage.py app/
COPY src app/src
COPY migrations app/migrations
RUN install -d -o nonroot -g nonroot -m 0700 data backups projects .ssh

# Root may only drive the unlock socket; everything else drops to the service
# account, so what `hoisty init` or `import` writes stays readable by the daemons.
COPY --chmod=0755 <<'EOF' /usr/bin/hoisty
#!/bin/sh
umask 077
case "$1" in unlock|status) ;; *)
  [ "$(id -u)" = 0 ] && exec setpriv --reuid=nonroot --regid=nonroot --init-groups "$0" "$@" ;;
esac
exec /var/lib/hoisty/venv/bin/python /var/lib/hoisty/app/manage.py "$@"
EOF

# Restart=on-failure in a loop rather than a container restart, so the container
# stays up for `hoisty init`, `migrate` and `unlock` while the daemon cannot start.
COPY --chmod=0755 <<'EOF' /usr/local/bin/entrypoint
#!/bin/sh
if [ "$1" = web ] && [ -n "$HOISTY_WEB_PROXY" ]; then
  set -- web --host 0.0.0.0 --port 8443 --behind-proxy
elif [ "$1" = web ]; then
  # ponytail: self-signed, new on every start; HOISTY_WEB_PROXY puts a domain's cert in front
  setpriv --reuid=nonroot --regid=nonroot --init-groups sh -c 'umask 077 &&
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 825 -subj /CN=localhost \
      -addext subjectAltName=DNS:localhost,IP:127.0.0.1 -keyout /tmp/key.pem -out /tmp/cert.pem 2>/dev/null'
  set -- web --host 0.0.0.0 --port 8443 --tls-cert /tmp/cert.pem --tls-key /tmp/key.pem
fi
trap 'kill -TERM "$pid" 2>/dev/null; wait "$pid"; exit 0' TERM INT
while :; do
  hoisty "$@" & pid=$!; wait "$pid"
  sleep 10 & pid=$!; wait "$pid"
done
EOF

ENV HOISTY_DATA=/var/lib/hoisty/data \
    HOME=/var/lib/hoisty \
    ANSIBLE_LOCAL_TEMP=/tmp/ansible \
    ANSIBLE_SSH_CONTROL_PATH_DIR=/tmp/ansible/cp \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
ENTRYPOINT ["entrypoint"]
CMD ["bot"]
