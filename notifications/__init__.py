"""
notifications package — public exports
"""
from notifications.slack_notifier import SlackNotifier, SlackConfig

try:
    from notifications.telegram_notifier import TelegramNotifier  # type: ignore[attr-defined]
except Exception:
    TelegramNotifier = None  # type: ignore[assignment]

try:
    from notifications.notifier_bus import NotifierBus  # type: ignore[attr-defined]
except Exception:
    NotifierBus = None  # type: ignore[assignment]

__all__ = [
    "SlackNotifier", "SlackConfig",
    "TelegramNotifier",
    "NotifierBus",
]
