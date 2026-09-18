#!/usr/bin/env bash
# Two listeners, because the camera rule differs by origin:
#   laptop -> http://localhost      already a secure context, no certificate, no warning
#   phone  -> https://<lan-ip>      getUserMedia refuses plain http:// over a LAN
set -e
cd "$(dirname "$0")"

IP=$(ip route get 1.1.1.1 | grep -oP 'src \K\S+' | head -1)

# The cert pins the IP. DHCP hands out a new one sooner or later, and the failure then
# looks like a broken camera rather than a stale certificate — so just reissue it.
if ! openssl x509 -in certs/cert.pem -noout -text 2>/dev/null | grep -q "IP Address:$IP"; then
  echo "issuing certificate for $IP"
  mkdir -p certs
  openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
    -keyout certs/key.pem -out certs/cert.pem -subj "/CN=adrover-dev" \
    -addext "subjectAltName=IP:$IP,IP:127.0.0.1,DNS:localhost" 2>/dev/null
  echo "note: the certificate changed, so your phone will warn again — accept it once more"
fi

echo
echo "  laptop : http://localhost:8100          (no warning)"
echo "  phone  : https://$IP:8443   (accept the certificate warning once)"
echo

.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8443 \
  --ssl-keyfile certs/key.pem --ssl-certfile certs/cert.pem &
HTTPS=$!
trap 'kill $HTTPS 2>/dev/null' EXIT

exec .venv/bin/uvicorn app:app --host 127.0.0.1 --port 8100
