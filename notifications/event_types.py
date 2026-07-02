"""
QuantLuna — Notifications: Event Types
Sprint 29

Define EventType enum si AlertEvent dataclass.
Fiecare eveniment are: tip, severitate, payload dict, timestamp.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


class EventType(str, Enum):
    # Trading events
    TRADE_OPEN      = "TRADE_OPEN"
    TRADE_CLOSE     = "TRADE_CLOSE"
    # Risk alerts
    DD_ALERT        = "DD_ALERT"
    SHARPE_DROP     = "SHARPE_DROP"
    # Multi-pair events
    PAIR_START      = "PAIR_START"
    PAIR_STOP       = "PAIR_STOP"
    HALT_CASCADE    = "HALT_CASCADE"
    # System
    SYSTEM_ERROR    = "SYSTEM_ERROR"
    SYSTEM_START    = "SYSTEM_START"
    TEST            = "TEST"


class Severity(str, Enum):
    INFO     = "info"      # verde
    WARNING  = "warning"   # galben
    CRITICAL = "critical"  # rosu


# Severitate default per event type
_EVENT_SEVERITY: Dict[EventType, Severity] = {
    EventType.TRADE_OPEN:   Severity.INFO,
    EventType.TRADE_CLOSE:  Severity.INFO,
    EventType.DD_ALERT:     Severity.WARNING,
    EventType.SHARPE_DROP:  Severity.WARNING,
    EventType.PAIR_START:   Severity.INFO,
    EventType.PAIR_STOP:    Severity.INFO,
    EventType.HALT_CASCADE: Severity.CRITICAL,
    EventType.SYSTEM_ERROR: Severity.CRITICAL,
    EventType.SYSTEM_START: Severity.INFO,
    EventType.TEST:         Severity.INFO,
}

# Emoji per event type (Telegram)
_EVENT_EMOJI: Dict[EventType, str] = {
    EventType.TRADE_OPEN:   "✅",
    EventType.TRADE_CLOSE:  "🟢",
    EventType.DD_ALERT:     "⚠️",
    EventType.SHARPE_DROP:  "📉",
    EventType.PAIR_START:   "▶️",
    EventType.PAIR_STOP:    "⏹️",
    EventType.HALT_CASCADE: "🔴",
    EventType.SYSTEM_ERROR: "💥",
    EventType.SYSTEM_START: "🚀",
    EventType.TEST:         "🧪",
}

# Discord embed color per severity (hex int)
_SEVERITY_COLOR: Dict[Severity, int] = {
    Severity.INFO:     0x00C853,   # verde
    Severity.WARNING:  0xFFAB00,   # amber
    Severity.CRITICAL: 0xD50000,   # rosu
}


@dataclass
class AlertEvent:
    event_type: EventType
    payload:    Dict[str, Any]                  = field(default_factory=dict)
    severity:   Optional[Severity]              = None
    timestamp:  datetime                        = field(default_factory=lambda: datetime.now(timezone.utc))
    source:     str                             = "quantluna"

    def __post_init__(self):
        if self.severity is None:
            self.severity = _EVENT_SEVERITY.get(self.event_type, Severity.INFO)

    @property
    def emoji(self) -> str:
        return _EVENT_EMOJI.get(self.event_type, "🟡")

    @property
    def color(self) -> int:
        return _SEVERITY_COLOR.get(self.severity, 0x00C853)  # type: ignore[arg-type]

    @property
    def title(self) -> str:
        return f"{self.emoji} {self.event_type.value.replace('_', ' ').title()}"

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "severity":   self.severity.value if self.severity else "info",
            "timestamp":  self.timestamp.isoformat(),
            "source":     self.source,
            "payload":    self.payload,
        }
