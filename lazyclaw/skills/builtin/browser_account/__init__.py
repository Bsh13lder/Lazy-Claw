"""Multi-account browser identity skills (Phase A — Goal Executor).

Four skills wrap :mod:`lazyclaw.browser.profile_resolver` +
:mod:`lazyclaw.browser.browser_settings`:

- :class:`RegisterBrowserAccountSkill` — register a slug/friendly_name
  pair, create the on-disk profile dir.
- :class:`ListBrowserAccountsSkill` — table of registered accounts.
- :class:`SwitchBrowserAccountSkill` — pin a domain to a slug.
- :class:`TuneBrowserCadenceSkill` — adjust per-domain action timing.
"""

from lazyclaw.skills.builtin.browser_account.register_skill import (
    RegisterBrowserAccountSkill,
)
from lazyclaw.skills.builtin.browser_account.list_skill import (
    ListBrowserAccountsSkill,
)
from lazyclaw.skills.builtin.browser_account.switch_skill import (
    SwitchBrowserAccountSkill,
)
from lazyclaw.skills.builtin.browser_account.tune_cadence_skill import (
    TuneBrowserCadenceSkill,
)

__all__ = [
    "RegisterBrowserAccountSkill",
    "ListBrowserAccountsSkill",
    "SwitchBrowserAccountSkill",
    "TuneBrowserCadenceSkill",
]
