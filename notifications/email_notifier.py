"""
notifications/email_notifier.py — SMTP email notifier for critical alerts.

Used as a fallback channel when Telegram / Slack / Discord are unavailable.
Supports TLS (port 587 STARTTLS) and SSL (port 465).

Config via .env:
  EMAIL_HOST=smtp.gmail.com
  EMAIL_PORT=587
  EMAIL_USER=you@gmail.com
  EMAIL_PASSWORD=app-password
  EMAIL_TO=alerts@yourcompany.com
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


@dataclass
class EmailConfig:
    host: str = "smtp.gmail.com"
    port: int = 587
    user: str = ""
    password: str = ""
    to: str = ""
    use_ssl: bool = False

    @classmethod
    def from_env(cls) -> "EmailConfig":
        import os
        return cls(
            host=os.getenv("EMAIL_HOST", "smtp.gmail.com"),
            port=int(os.getenv("EMAIL_PORT", "587")),
            user=os.getenv("EMAIL_USER", ""),
            password=os.getenv("EMAIL_PASSWORD", ""),
            to=os.getenv("EMAIL_TO", ""),
            use_ssl=os.getenv("EMAIL_SSL", "false").lower() == "true",
        )


class EmailNotifier:
    """
    Synchronous SMTP email notifier.

    Use only for critical/rare alerts (drawdown breach, emergency stop,
    API key expiry). Not suited for high-frequency trade notifications.
    """

    def __init__(self, config: EmailConfig | None = None) -> None:
        self._cfg = config or EmailConfig.from_env()

    @property
    def enabled(self) -> bool:
        return bool(self._cfg.user and self._cfg.password and self._cfg.to)

    def send(self, subject: str, body: str, html: bool = False) -> bool:
        """
        Send email. Returns True on success, False on failure.
        Does NOT raise — safe to call from exception handlers.
        """
        if not self.enabled:
            logger.debug("EmailNotifier disabled (EMAIL_USER/PASSWORD/TO not set)")
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[QuantLuna] {subject}"
        msg["From"] = self._cfg.user
        msg["To"] = self._cfg.to

        part = MIMEText(body, "html" if html else "plain", "utf-8")
        msg.attach(part)

        try:
            if self._cfg.use_ssl:
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(self._cfg.host, self._cfg.port, context=context) as srv:
                    srv.login(self._cfg.user, self._cfg.password)
                    srv.sendmail(self._cfg.user, self._cfg.to, msg.as_string())
            else:
                with smtplib.SMTP(self._cfg.host, self._cfg.port) as srv:
                    srv.ehlo()
                    srv.starttls()
                    srv.login(self._cfg.user, self._cfg.password)
                    srv.sendmail(self._cfg.user, self._cfg.to, msg.as_string())

            logger.info("Email sent: %s -> %s", subject, self._cfg.to)
            return True

        except Exception as exc:
            logger.error("EmailNotifier failed: %s", exc)
            return False

    async def send_async(self, subject: str, body: str, html: bool = False) -> bool:
        """Async wrapper — runs send() in executor to avoid blocking event loop."""
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.send, subject, body, html)
