#!/usr/bin/env bash
# Keeps a DuckDNS subdomain pointed at this Mac's current public IPv4.
#
# We detect our IPv4 explicitly and send it, rather than leaving `ip=` blank for
# DuckDNS to auto-detect. Blank auto-detect uses the SOURCE ADDRESS of the
# request, which can be an IPv6 address (leaving the A record stale) or — with a
# VPN up on the host — the VPN's exit IP, silently pointing the domain at an
# unreachable address. Sending our own `-4` IPv4 is deterministic.
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

# `-4` pins the lookup to IPv4 so we never send an IPv6 address as the A record.
PUB="$(curl -4 -fsS -m8 https://api.ipify.org || true)"
RESP="$(curl -fsS "https://www.duckdns.org/update?domains=${DUCKDNS_DOMAIN}&token=${DUCKDNS_TOKEN}&ip=${PUB}" || echo "KO")"
echo "$(date -u +%FT%TZ) duckdns(${DUCKDNS_DOMAIN}): ${RESP}"
[ "$RESP" = "OK" ]
