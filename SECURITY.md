# Security Architecture

LazyClaw encrypts all user content at rest using AES-256-GCM with envelope encryption.
This document explains the threat model, key hierarchy, and operational procedures.

---

## Threat Model

| Attack scenario | What the attacker gets | What remains protected |
|---|---|---|
| **Database stolen** | Encrypted blobs (`enc:v1:…` / `enc:v2:…`), usernames, bcrypt password hashes, timestamps, IDs | All user content — messages, memories, credentials, tasks. Brute-forcing bcrypt doesn't help (password hashes are not used for encryption). |
| **Database + `.env` stolen** | `SERVER_SECRET` → can derive wrapping keys → can unwrap DEKs → **all user data decryptable** | Nothing. This is the worst case. Mitigation: keep `.env` on encrypted disk, restrict file permissions (`chmod 600`), use a secrets manager in production. |
| **Process memory dump** | Cached DEKs (up to 128, TTL 1 hour), plaintext currently being processed | DEKs for users who haven't been active recently (evicted from cache). Data on disk remains encrypted. |
| **Network MITM** | Session cookies (if TLS not enforced), API responses in transit | Data at rest. Mitigation: always run behind TLS in production (HTTPS). |

**Bottom line:** An attacker who gets _only_ the database cannot read user data. An attacker who gets the database _and_ `SERVER_SECRET` can read everything. Protect `SERVER_SECRET` like a root credential.

---

## Key Hierarchy

```
SERVER_SECRET (environment variable, ≥32 chars)
    │
    ├─ Per-user wrapping key
    │   = PBKDF2(SERVER_SECRET + user_id, per_user_salt, 600K iterations, SHA-256)
    │   │
    │   └─ Wraps the user's DEK (AES-256-GCM, AAD = "dek-wrap-v1")
    │
    └─ Legacy derived key (v1 — migration only)
        = PBKDF2(SERVER_SECRET + user_id, FIXED_SALT, 100K iterations)
        → Becomes the user's DEK on first access (auto-migration)

Per-user DEK (Data Encryption Key)
    = Random 256-bit key, generated at registration
    │
    ├─ Encrypts ALL user data:
    │   messages, memories, credentials, tasks, traces, etc.
    │
    └─ Stored encrypted in users.encrypted_dek column
```

### Key properties

| Property | Value |
|---|---|
| **Algorithm** | AES-256-GCM (authenticated encryption) |
| **KDF** | PBKDF2-HMAC-SHA256 |
| **Iterations** | 600,000 (OWASP 2024 recommendation) |
| **Per-user salt** | `secrets.token_urlsafe(16)` (~106 bits), stored in `users.encryption_salt` |
| **Nonce** | 12 bytes from `os.urandom()` per encryption |
| **AAD** | v2 format binds ciphertext to `user:{user_id}:{context}` |
| **DEK size** | 256 bits (random) |
| **Wrapping AAD** | `dek-wrap-v1` (prevents confusion with data encryption) |

---

## Encryption Formats

### v1 (legacy)
```
enc:v1:{base64_nonce}:{base64_ciphertext}
```
- No AAD (ciphertext not bound to user/context)
- Used by pre-hardening code
- Readable by current code; new writes produce v2 where AAD is provided

### v2 (current)
```
enc:v2:{base64_nonce}:{base64_ciphertext}
```
- AAD binds ciphertext to user and context (e.g., `user:abc123:vault:api_key`)
- Prevents swapping encrypted values between users or between fields
- An attacker who copies a v2 ciphertext to a different user's row will fail decryption

### Wrapped DEK
```
wrapped:v1:{base64_nonce}:{base64_ciphertext}
```
- Stored in `users.encrypted_dek`
- AAD = `dek-wrap-v1`

---

## Envelope Encryption Benefits

1. **Key rotation:** Changing `SERVER_SECRET` only requires re-wrapping DEKs (one AES operation per user), not re-encrypting all data.
2. **Password change isolation:** If user-password-derived wrapping is added in the future, changing a password only re-wraps the DEK.
3. **Per-user isolation:** Each user's DEK is independent. Compromising one user's DEK does not affect others (unless the wrapping key is also compromised).
4. **Efficient migration:** Legacy users are auto-migrated on first access — their old derived key becomes their DEK, wrapped with the new wrapping key.

---

## What's Encrypted vs. Plaintext

### Encrypted (AES-256-GCM with user DEK)

| Table | Encrypted columns |
|---|---|
| `agent_messages` | `content` |
| `credential_vault` | `value` |
| `personal_memory` | `content` |
| `daily_logs` | `summary`, `key_events` |
| `site_memory` | `title`, `content` |
| `tasks` | `title`, `description`, `context` |
| `agent_jobs` | `instruction`, `context` |
| `mcp_connections` | `config` |
| `agent_traces` | `content` |
| `agent_team_messages` | `content` |
| `background_tasks` | `instruction`, `result` |
| `survival_gigs` | `title`, `description`, `proposal_text`, `deliverable_summary` |

### Plaintext (required for queries/indexing)

| Data | Reason |
|---|---|
| `user_id` | Foreign key, query scoping |
| `username` | Login lookup |
| `password_hash` | Authentication (bcrypt) |
| `encryption_salt` | Key derivation input |
| Timestamps | Ordering, expiry checks |
| Status fields | Filtering (active/completed/pending) |
| IDs (UUIDs) | Primary keys, joins |
| Cron expressions | Scheduling |
| Model assignments | Feature routing |

---

## Memory Protection

### DEK cache
- **Max size:** 128 entries
- **TTL:** 1 hour (entries expire and are zeroed)
- **Logout:** `clear_user_dek(user_id)` zeros and removes the cached DEK
- **Shutdown:** `clear_all_deks()` zeros all cached DEKs

### Legacy key cache
- **Max size:** 64 entries
- **TTL:** 1 hour
- **Eviction:** LRU with TTL, thread-safe

### Best-effort zeroing
- `secure_zero()` uses `ctypes.memset` to overwrite `bytearray` buffers
- Python's GC does not guarantee immediate collection of `bytes` objects
- DEKs are stored as `bytearray` in the cache for zeroability
- Zeroing is best-effort — Python is not a constant-time-safe language

---

## Operational Procedures

### Rotating SERVER_SECRET

```python
from lazyclaw.crypto.key_manager import rotate_server_secret
from lazyclaw.config import load_config

config = load_config()
count = await rotate_server_secret(config, new_secret="your-new-secret-here")
print(f"Re-wrapped {count} user DEKs")
```

After rotation:
1. Update `SERVER_SECRET` in your `.env` file
2. Restart the server
3. The old secret is no longer needed

**What happens:** Each user's DEK is unwrapped with the old secret, then re-wrapped with the new secret. The DEKs themselves don't change, so all encrypted data remains readable.

**Cost:** One PBKDF2 derivation (old) + one PBKDF2 derivation (new) + one AES unwrap + one AES wrap per user. For 100 users ≈ 2 minutes.

### Adding a new user

Handled automatically by `register_user()`:
1. Generates random `encryption_salt`
2. Generates random 256-bit DEK
3. Wraps DEK with server-derived wrapping key
4. Stores wrapped DEK in `users.encrypted_dek`

### Legacy migration

Users created before the hardening update have `encrypted_dek = NULL`. On their first data access:
1. The old derived key (`PBKDF2(SERVER_SECRET + user_id, FIXED_SALT, 100K)`) is computed
2. This becomes their DEK
3. The DEK is wrapped with the new wrapping key (`PBKDF2(SERVER_SECRET + user_id, per_user_salt, 600K)`)
4. The wrapped DEK is stored in `users.encrypted_dek`
5. Future accesses use the DEK path

This is transparent — no manual migration needed.

---

## User Responsibilities

1. **Strong password:** The bcrypt hash protects login. A weak password allows account takeover (but not direct decryption — encryption uses server-derived keys, not user passwords).
2. **Secure `.env`:** `SERVER_SECRET` must be kept secret. If leaked, all data is at risk.
3. **TLS in production:** Without HTTPS, session cookies and API responses are visible to network attackers.
4. **Backup encryption:** Database backups contain encrypted data. The backups are safe without `SERVER_SECRET`, but store them securely anyway.

---

## Comparison with OpenClaw

| Feature | LazyClaw | OpenClaw |
|---|---|---|
| Data at rest | AES-256-GCM encrypted | Plaintext |
| Key derivation | PBKDF2 600K iterations, per-user salt | N/A |
| Envelope encryption | Yes (DEK pattern) | No |
| Key rotation | Yes (re-wrap DEKs) | N/A |
| AAD binding | Yes (v2 format) | No |
| Credential vault | Encrypted | Plaintext `.env` |
| Memory protection | TTL cache, zeroing | No |

---

## Future Improvements

1. **User-password-derived wrapping:** Add a second wrapping layer where the user's password (not just SERVER_SECRET) protects their DEK. This would mean the server truly cannot decrypt without the user's password — but breaks daemon operations (cron jobs, Telegram messages) when the user is offline.
2. **Argon2id:** Replace PBKDF2 with Argon2id for memory-hard key derivation (stronger against GPU attacks).
3. **HSM integration:** Store SERVER_SECRET in a hardware security module instead of `.env`.
4. **Encrypted user IDs:** Replace plaintext `user_id` in tables with encrypted or hashed identifiers to prevent ownership analysis.
5. **Forward secrecy:** Per-session ephemeral keys so compromising one session doesn't expose others.
