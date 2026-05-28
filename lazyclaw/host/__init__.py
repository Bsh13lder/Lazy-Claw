"""Host-side bridges the container talks to over ``host.docker.internal``.

macOS-host-only capabilities (Metal STT, sleep/wake control) can't run inside
the Linux container, so small launchd-managed FastAPI services run on the host
and the container calls them. This package holds the *client* side.
"""
