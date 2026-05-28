# Caddy built with the Cloudflare DNS plugin so it can solve the ACME
# DNS-01 challenge (writes the _acme-challenge TXT record via the
# Cloudflare API). DNS-01 needs no inbound port — the cert is issued
# even though app.lazytasker.com only ever resolves to a private VPN IP.
#
# The official caddy image does NOT ship DNS provider modules, so we
# rebuild it with xcaddy. Pinned to the 2-builder tag.
FROM caddy:2-builder AS builder
RUN xcaddy build \
    --with github.com/caddy-dns/cloudflare

FROM caddy:2
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
COPY Caddyfile /etc/caddy/Caddyfile
