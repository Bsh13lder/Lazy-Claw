# Remote Access — Installable Mobile PWA, From Anywhere, No Third Party

> **CURRENT SETUP (2026-06-30): DuckDNS + Caddy over a forwarded router port.**
> Fully self-owned — no VPN on the phone, no third party in the data path.
> Data path: `phone --TLS--> home router (:443) --> Mac:443 --> Caddy (TLS
> terminates HERE) --> lazyclaw-web (nginx → gateway)`. DuckDNS only maps the
> name → home IP; Let's Encrypt issues the cert over the forwarded port (no
> Cloudflare, no DNS-01). Replaces the ngrok stopgap (which decrypted at its edge).
>
> **Pieces:** app pinned to `https://lazyclaw.duckdns.org`
> (`mobile/.../app_constants.dart`) · `caddy/Caddyfile` (default ACME) ·
> `caddy/Dockerfile` (plain `caddy:2`) · `.env` `LAZYCLAW_DOMAIN`/`ACME_EMAIL` ·
> `scripts/duckdns-update.sh` + `scripts/install-duckdns-updater.sh` (keep the
> subdomain on the home IP).
>
> **Manual prereqs (one-time):** ① register the DuckDNS subdomain + token →
> `~/.lazyclaw/duckdns.env`; ② router: reserve the Mac at its LAN IP and forward
> TCP **80 + 443** to it, **disable UPnP**; ③ `docker compose up -d` so nginx +
> gateway run and Caddy can fetch the cert. Hardening: only :443 is exposed
> (Caddy is the single entry point); add Caddy `basic_auth`/`rate_limit` +
> fail2ban/CrowdSec as desired. **Trade-off:** the home IP becomes publicly
> resolvable — accepted in exchange for zero third-party decryption.
>
> The WireGuard + Cloudflare-DNS-01 design below is the prior model, kept for
> reference.

---

Goal: open LazyClaw on your phone from **anywhere** (cellular too), install it as a
real app (icon + fullscreen), with **no company ever decrypting your traffic**.

The architecture has two independent layers:

| Layer | Tool | Job | License |
|-------|------|-----|---------|
| **Cert / HTTPS** | Caddy + Cloudflare DNS | Real Let's Encrypt cert so the PWA installs | Apache-2.0 |
| **Reachability** | WireGuard (or VPS relay) | Phone reaches the home Mac from anywhere | GPLv2 |

> **Why both?** PWA install requires trusted HTTPS — that's the Caddy layer.
> Reaching a Mac behind your router from cellular requires a VPN — that's the
> WireGuard layer. Tailscale bundles both; here we self-host both so nobody
> sees your data. Cloudflare only ever sees an `_acme-challenge` TXT record at
> cert-renewal time — **never** your application traffic (that stays inside the
> WireGuard tunnel).

---

## Part 0 — Which reachability case are you in?

```
Public IP (what the internet sees):  86.127.227.121   ← NOT a CGNAT range ✅
```

Open `http://192.168.1.1` (your router) → find **WAN IP / Internet IP**:

- **WAN IP == `86.127.227.121`** → **Case A**: no CGNAT. Use plain WireGuard on the
  Mac. No VPS, no third party at all. **(Follow Parts 1–5.)**
- **WAN IP is `100.64.x.x` / `10.x.x.x` / differs** → **Case B**: CGNAT. You need a
  tiny VPS as the meeting point. **(Follow Parts 1–3, then Part 6.)**

---

## Part 1 — Move lazytasker.com DNS to Cloudflare (5 min, free)

The cert automation needs an API-driven DNS host. `register.domains` has none, so we
point the nameservers at Cloudflare (domain stays registered at register.domains).

1. Create a free account at <https://dash.cloudflare.com> → **Add a site** →
   `lazytasker.com` → Free plan.
2. Cloudflare scans existing records. Your old site is down, so there's likely nothing
   to preserve — but **double-check** for any `MX` (email) records and re-add them if
   you use email on this domain.
3. Cloudflare shows two nameservers (e.g. `xena.ns.cloudflare.com`). Log into
   **register.domains → lazytasker.com → Nameservers**, replace with Cloudflare's two.
4. Wait for the zone to go **Active** (minutes to a few hours).

## Part 2 — Create a scoped Cloudflare API token

<https://dash.cloudflare.com/profile/api-tokens> → **Create Token** → *Custom token*:

- **Permissions:** `Zone` → `DNS` → `Edit`
- **Zone Resources:** `Include` → `Specific zone` → `lazytasker.com`

Copy the token. This token can **only** edit DNS on this one zone — it cannot read
traffic or touch anything else.

## Part 3 — Fill `.env` and verify Caddy builds

In the repo `.env` (copy from `.env.example` if needed):

```dotenv
LAZYCLAW_DOMAIN=app.lazytasker.com
ACME_EMAIL=you@example.com
CLOUDFLARE_API_TOKEN=<the scoped token from Part 2>
```

> `.env` is gitignored — the token never enters version control.

The cert is only issued once Caddy can reach the app over the VPN-resolved hostname,
so finish the WireGuard part first, then bring the stack up (Part 5 step 4).

---

## Part 4 — WireGuard on the Mac (Case A)

Install tools and generate keys:

```bash
brew install wireguard-tools
mkdir -p ~/.lazyclaw/wg && cd ~/.lazyclaw/wg
wg genkey | tee mac.key | wg pubkey > mac.pub
wg genkey | tee phone.key | wg pubkey > phone.pub
chmod 600 *.key
```

Create `/opt/homebrew/etc/wireguard/wg0.conf` (Mac = `10.13.13.1`):

```ini
[Interface]
Address = 10.13.13.1/24
ListenPort = 51820
PrivateKey = <contents of mac.key>

[Peer]
# Phone
PublicKey = <contents of phone.pub>
AllowedIPs = 10.13.13.2/32
```

Bring it up (and on boot):

```bash
sudo wg-quick up wg0
sudo brew services start wireguard-tools   # optional: start at login
```

**Router port-forward:** forward **UDP 51820** → `192.168.1.172` (the Mac). This is
the one inbound rule you need. (Reserve `192.168.1.172` as a static DHCP lease so it
never moves.)

**Phone config** — give it to the WireGuard app (Part 5). Save as `phone.conf`:

```ini
[Interface]
Address = 10.13.13.2/24
PrivateKey = <contents of phone.key>
DNS = 1.1.1.1

[Peer]
PublicKey = <contents of mac.pub>
Endpoint = 86.127.227.121:51820
# Split tunnel: ONLY lazyclaw traffic goes home; everything else stays on
# your normal connection. Use 0.0.0.0/0 instead if you want a full tunnel.
AllowedIPs = 10.13.13.0/24
PersistentKeepalive = 25
```

## Part 5 — Split-horizon DNS, bring up, install

1. **Point the hostname at the Mac's tunnel IP.** In Cloudflare DNS, add an **A record**:
   - Name: `app`  •  Content: `10.13.13.1`  •  Proxy status: **DNS only (grey cloud)**

   Any device on the WireGuard tunnel that opens `app.lazytasker.com` is routed to the
   Mac over the tunnel. (Off-tunnel, it resolves to a private IP and simply won't
   connect — harmless.) Let's Encrypt ignores this A record; it only reads the TXT.

2. **Bring up the stack:**
   ```bash
   docker compose up -d --build caddy lazyclaw-web
   docker compose logs -f caddy     # watch for "certificate obtained successfully"
   ```

3. **Phone:** install **WireGuard** (Play Store, official, open-source) → import
   `phone.conf` (scan a QR with `qrencode -t ansiutf8 < phone.conf`, or transfer the
   file) → toggle the tunnel **on**.

4. **Install the PWA:** open Chrome on the phone → `https://app.lazytasker.com` → you
   get a valid lock 🔒 → tap the **Install LazyClaw** pill (or Chrome menu → *Install
   app*). Icon lands on your home screen, opens fullscreen.

Done — works on cellular, nothing public, no company decrypts anything.

---

## Part 6 — CGNAT fallback (Case B)

If your WAN IP is CGNAT, the Mac can't accept inbound, so add a ~€4/mo VPS as the
rendezvous. Two options:

- **WireGuard on the VPS** + the Mac connects out to it + the phone connects to the
  VPS; route `10.13.13.0/24` so the phone reaches the Mac through the VPS. Caddy still
  runs on the Mac and holds the cert.
- **Headscale on the VPS** (BSD-3) as the coordination server + the official Tailscale
  app on the Mac and phone. More moving parts but nicer device management.

Tell me which and I'll write the exact configs. Everything in Parts 1–3 (Cloudflare +
token + Caddy) is unchanged.

---

## Renewal & maintenance

- Caddy auto-renews the cert (~every 60 days) via DNS-01 — nothing to do.
- Certs persist in the `caddy_data` Docker volume, so restarts don't re-hit Let's
  Encrypt rate limits.
- If you change the Mac's tunnel IP, update the Cloudflare `app` A record to match.
