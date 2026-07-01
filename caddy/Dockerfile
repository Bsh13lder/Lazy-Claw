# Caddy built with the DuckDNS DNS plugin so it can solve the ACME DNS-01
# challenge (writes _acme-challenge TXT via the DuckDNS API). DNS-01 needs NO
# inbound port — essential here because the Vodafone router reserves public
# 80/443, so the cert can't be issued over those ports. The official caddy
# image ships no DNS modules, so we rebuild it with xcaddy.
FROM caddy:2-builder AS builder
RUN xcaddy build \
    --with github.com/caddy-dns/duckdns

FROM caddy:2
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
COPY Caddyfile /etc/caddy/Caddyfile
