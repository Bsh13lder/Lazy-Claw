# Cyber Verification Program (CVP) — application draft

Draft text for Anthropic's Cyber Verification Program / cyber-use-case
exemption. It exists to unblock **legitimate** security-adjacent work for
verified organizations. Submit at the entry the error message links
(`https://claude.com/form/cyber-use-case`) or the API/Code program portal
(`portal.anthropic.com/programs/cvp`). Verification is **per-organization**
(not per-API-key) and typically takes ~2 business days.

> Fill in the bracketed fields before submitting. Everything below is written
> to be accurate — do not add claims that aren't true of your setup.

---

**Organization / applicant:** [your name / org], solo maintainer of LazyClaw —
an open-source (MIT), end-to-end-encrypted personal AI agent platform.

**Contact:** [email]

**Models / surface:** Claude (Opus/Sonnet) via the Claude Code subscription,
used as the reasoning engine for an autonomous personal-assistant agent.

**What we are building:** A personal AI assistant that operates the *user's own*
web admin panels on their behalf. A bridge component ("apihunter") records a
panel's backend endpoints **once**, using the user's **own authenticated
browser session**, so routine admin tasks — publishing a blog post, reading
the site's analytics, updating stock — run as direct authenticated API calls
instead of the model clicking through the UI every time.

**Why it can read as security-sensitive (and why it is not):** Recording a
panel's endpoints and reusing an already-logged-in session is *shaped* like
reconnaissance tooling, so the model's cybersecurity safeguard has declined it.
In fact it is strictly **first-party operational automation**: the operator is
automating a system they **own or administer** and are already authenticated
to. There is no scanning of third-party systems, no credential acquisition, and
no attempt to gain unauthorized access.

**Authorization controls already enforced in the product:**
- **Owner-confirmation gate (fail-closed):** apihunter refuses to record a new
  panel without an explicit `owner_confirmed` signal tied to the user
  confirming they own/administer it. Unowned targets fail closed.
- **Session-bound:** recording and replay use the user's own live browser
  session — the automation can only touch panels the user is already logged
  into as themselves.
- **Per-user encryption & isolation:** all recorded manifests are AES-256-GCM
  encrypted per user; no cross-user access.
- **Human-in-the-loop on writes:** state-changing calls (publish / update /
  delete / pay / send) are gated behind explicit user confirmation checkpoints.

**Scope of the request:** Permission for the assistant to perform first-party
administrative automation (endpoint discovery + authenticated API calls) on web
panels the operator owns or administers, on the operator's own account.

**Out of scope / not requested:** any access to systems the operator does not
own; vulnerability scanning; credential harvesting; offensive security tooling.

---

## Notes for the maintainer (not part of the application)

- Verification lands at the **org identity** layer — the durable fix, vs.
  rewording prompts (which research shows can *increase* refusals and breaks on
  model updates).
- Field reports note approvals occasionally lag propagating to the API/Code
  surface — pair CVP with the in-product graceful-refusal handling (already
  shipped: a `ContentPolicyRefusal` card that names this exemption path) so a
  refusal is always legible, never an opaque "hiccup".
- ZDR (zero-data-retention) orgs may be ineligible — check your org settings.
