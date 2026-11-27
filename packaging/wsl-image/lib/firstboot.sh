#!/usr/bin/env bash
# First boot of an imported Guaardvark WSL image: give this machine its own TLS
# key for PostgreSQL (the image ships none). Runs once, before PostgreSQL starts.
# Guaardvark's own secrets (SECRET_KEY, the database password) are created by
# start.sh on its first run, as on any fresh install.
#
# Same files make-ssl-cert writes, made with openssl directly: make-ssl-cert goes
# through debconf, whose database Ubuntu's landscape-config holds at first boot.
set -euo pipefail
pem=/etc/ssl/certs/ssl-cert-snakeoil.pem
key=/etc/ssl/private/ssl-cert-snakeoil.key
host="$(hostname -f 2>/dev/null || hostname)"
openssl req -new -x509 -days 3650 -nodes -sha256 -newkey rsa:2048 \
    -subj "/CN=$host" -addext "subjectAltName=DNS:$host" -keyout "$key" -out "$pem" 2>/dev/null
chmod 644 "$pem"
chmod 640 "$key"
chown root:ssl-cert "$key"
ln -sf ssl-cert-snakeoil.pem "/etc/ssl/certs/$(openssl x509 -hash -noout -in "$pem").0"
rm -f /var/lib/guaardvark-image/firstboot-pending
