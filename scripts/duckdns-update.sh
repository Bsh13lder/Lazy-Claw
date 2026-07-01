#!/usr/bin/env bash
# Keeps a DuckDNS subdomain pointed at this Mac's current public IP.
#
# DuckDNS auto-detects the caller's public IP when `ip=` is left blank, so no
# IP lookup is needed — the request itself comes from the home connection.
#
# Reads two values from ~/.lazyclaw/duckdns.env (override path with DUCKDNS_CONF):
#   DUCKDNS_DOMAIN=lazyclaw      # the subdomain LABEL only (no .duckdns.org)
#   DUCKDNS_TOKEN=xxxxxxxx-xxxx-...     # your DuckDNS account token
#
# Returns 0 only when DuckDNS replies "OK". Logs one line per run.
set -euo pipefail

CONF="${DUCKDNS_CONF:-$HOME/.lazyclaw/duckdns.env}"
if [ -f "$CONF" ]; then
	# shellcheck disable=SC1090
	. "$CONF"
fi
: "${DUCKDNS_DOMAIN:?set DUCKDNS_DOMAIN in $CONF (subdomain label, e.g. lazyclaw)}"
: "${DUCKDNS_TOKEN:?set DUCKDNS_TOKEN in $CONF}"

RESP="$(curl -fsS "https://www.duckdns.org/update?domains=${DUCKDNS_DOMAIN}&token=${DUCKDNS_TOKEN}&ip=" || echo "KO")"
echo "$(date -u +%FT%TZ) duckdns(${DUCKDNS_DOMAIN}): ${RESP}"
[ "$RESP" = "OK" ]
