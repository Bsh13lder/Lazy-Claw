"""Instagram session manager with anti-ban measures."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from instagrapi import Client
from instagrapi.exceptions import (
    ChallengeRequired,
    LoginRequired,
    TwoFactorRequired,
)

logger = logging.getLogger(__name__)


class InstagramSessionManager:
    """Manages Instagram login, session persistence, device fingerprints, and challenges."""

    def __init__(
        self,
        session_dir: str,
        username: str,
        password: str,
        proxy: str = "",
        totp_seed: str = "",
    ):
        self.session_dir = Path(session_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.session_file = self.session_dir / "session.json"
        self.device_file = self.session_dir / "device.json"
        self.username = username
        self.password = password
        self.proxy = proxy
        self.totp_seed = totp_seed
        self.pending_challenge: dict | None = None

        self.client = Client()
        self.client.delay_range = [1, 3]

        if proxy:
            self.client.set_proxy(proxy)

        self._load_or_generate_device()

    def _load_or_generate_device(self):
        if self.device_file.exists():
            try:
                with open(self.device_file) as f:
                    device = json.load(f)
                self.client.set_device(device)
                logger.info("Loaded persisted device fingerprint")
                return
            except Exception as e:
                logger.warning(f"Failed to load device: {e}")

        device = self.client.get_settings().get("device_settings", {})
        if device:
            with open(self.device_file, "w") as f:
                json.dump(device, f)
            logger.info("Generated and persisted new device fingerprint")

    def login(self) -> bool:
        if not self.username or not self.password:
            logger.error("No Instagram credentials provided")
            return False

        if self.session_file.exists():
            try:
                self.client.load_settings(str(self.session_file))
                self.client.login(self.username, self.password)
                self.client.get_timeline_feed()
                logger.info("Restored existing Instagram session")
                self._save_session()
                return True
            except LoginRequired:
                logger.info("Saved session expired, doing fresh login")
                self.client = Client()
                if self.proxy:
                    self.client.set_proxy(self.proxy)
                self.client.delay_range = [1, 3]
                self._load_or_generate_device()
            except Exception as e:
                logger.warning(f"Session restore failed: {e}")

        try:
            self.client.login(self.username, self.password)
            self._save_session()
            self.pending_challenge = None
            logger.info("Fresh Instagram login successful")
            return True
        except TwoFactorRequired:
            if self.totp_seed:
                try:
                    import pyotp

                    totp = pyotp.TOTP(self.totp_seed)
                    code = totp.now()
                    self.client.two_factor_login(code)
                    self._save_session()
                    self.pending_challenge = None
                    logger.info("2FA login successful (TOTP)")
                    return True
                except Exception as e:
                    logger.error(f"2FA TOTP failed: {e}")
                    self.pending_challenge = {
                        "type": "2fa",
                        "message": "2FA TOTP failed. Check your seed.",
                    }
                    return False
            else:
                logger.error("2FA required but no TOTP seed configured")
                self.pending_challenge = {
                    "type": "2fa",
                    "message": "2FA required. Enter code or provide TOTP seed.",
                }
                return False
        except ChallengeRequired:
            logger.warning("Challenge required by Instagram")
            try:
                self.client.challenge_resolve(self.client.last_json)
            except Exception:
                pass
            self.pending_challenge = {
                "type": "challenge",
                "message": "Verification code sent to your email/phone. Use instagram_verify tool to submit it.",
            }
            return False
        except Exception as e:
            logger.error(f"Instagram login failed: {e}")
            return False

    def resolve_challenge(self, code: str) -> bool:
        try:
            if self.pending_challenge and self.pending_challenge.get("type") == "2fa":
                self.client.two_factor_login(code)
            else:
                self.client.challenge_code_handler = lambda *args, **kwargs: code
                self.client.challenge_resolve(self.client.last_json)
            self._save_session()
            self.pending_challenge = None
            logger.info("Challenge resolved successfully")
            return True
        except Exception as e:
            logger.error(f"Challenge resolution failed: {e}")
            return False

    def _save_session(self):
        try:
            self.client.dump_settings(str(self.session_file))
            device = self.client.get_settings().get("device_settings", {})
            if device:
                with open(self.device_file, "w") as f:
                    json.dump(device, f)
        except Exception as e:
            logger.warning(f"Failed to save session: {e}")

    def is_logged_in(self) -> bool:
        try:
            self.client.get_timeline_feed()
            return True
        except Exception:
            return False

    def get_pending_dms(self, limit: int = 20, unread_only: bool = True) -> list:
        try:
            if unread_only:
                threads = self.client.direct_threads(
                    amount=limit, selected_filter="unread"
                )
            else:
                threads = self.client.direct_threads(amount=limit)
            return threads
        except LoginRequired:
            logger.warning("Session expired during DM poll, re-logging in")
            if not self.login():
                self.pending_challenge = self.pending_challenge or {
                    "type": "session_expired",
                    "message": "Session expired and re-login failed. Run instagram_setup again.",
                }
            return []
        except Exception as e:
            logger.error(f"Failed to fetch DMs: {e}")
            return []
