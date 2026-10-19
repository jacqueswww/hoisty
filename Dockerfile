# syntax=docker/dockerfile:1
# Docker Hardened Image. The -dev variant, not the distroless one: ansible,
# git-askpass.sh and the scripts below all need /bin/sh. Pull needs `docker login dhi.io`.
FROM dhi.io/python:3.12-debian13-dev

RUN apt-get update \
 && apt-get install -y --no-install-recommends git openssh-client openssl \
 && rm -rf /var/lib/apt/lists/*

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
# ponytail: self-signed, new on every start; mount a real cert before exposing on a domain
[ "$1" = web ] && setpriv --reuid=nonroot --regid=nonroot --init-groups sh -c 'umask 077 &&
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 825 -subj /CN=localhost \
    -addext subjectAltName=DNS:localhost,IP:127.0.0.1 -keyout /tmp/key.pem -out /tmp/cert.pem 2>/dev/null'
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
