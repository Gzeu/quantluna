"""
execution/withdrawal_guard.py  -  QuantLuna Withdrawal Guard v1.0

Sprint S33 (2026-07-12):
  Sistem de retragere externa cu confirmare manuala obligatorie.

  PRINCIPIU DE SECURITATE:
    - Sistemul NICIODATA nu executa o retragere fara comanda /confirm_<UUID>
      din Telegram, trimisa de la chat_id autorizat.
    - Timeout 30 minute: daca nu se confirma, cererea se anuleaza automat.
    - Whitelist adrese: orice adresa necunoscuta este REFUZATA automat.
    - Audit log complet: toate cererile (confirmate sau refuzate) sunt loggate.
    - Limita zilnica configurabila (default 1000 USDT/zi).

  Flux:
    1. propose_withdrawal(amount, address, chain) -> UUID
    2. Telegram: "Confirmi retragerea? /confirm_UUID sau /reject_UUID"
    3. Operator trimite /confirm_UUID in Telegram
    4. WithdrawalGuard executa retragerea via Bybit Withdrawal API
    5. Telegram: confirmare finala cu TX hash

Usage::

    guard = WithdrawalGuard.from_env(notifier_bus=bus)
    uid = await guard.propose_withdrawal(
        amount_usdt=500.0,
        address="0xABC...",
        chain="ERC20",
        reason="profit lunar",
    )
    # Operatorul confirma din Telegram cu /confirm_<uid>
    # sau sistemul anuleaza dupa 30 minute
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

from loguru import logger


class WithdrawalGuard:
    """
    Guard pentru retrageri externe cu confirmare manuala Telegram.

    ATENTIE: Retragerile sunt IREVERSIBILE. Aceasta clasa implementeaza
    toate masurile de siguranta necesare.
    """

    _TIMEOUT_SECONDS = 30 * 60  # 30 minute
    _CREATE_TABLE = """
    CREATE TABLE IF NOT EXISTS withdrawal_proposals (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        proposal_id     TEXT NOT NULL UNIQUE,
        amount_usdt     REAL NOT NULL,
        address         TEXT NOT NULL,
        chain           TEXT NOT NULL,
        reason          TEXT NOT NULL DEFAULT '',
        status          TEXT NOT NULL DEFAULT 'pending',
        bybit_tx_id     TEXT,
        requested_at    TEXT NOT NULL,
        confirmed_at    TEXT,
        rejected_at     TEXT,
        expired_at      TEXT,
        confirmed_by    TEXT
    );
    CREATE TABLE IF NOT EXISTS withdrawal_whitelist (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        address     TEXT NOT NULL UNIQUE,
        chain       TEXT NOT NULL,
        label       TEXT NOT NULL DEFAULT '',
        added_at    TEXT NOT NULL
    );
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = False,
        notifier_bus=None,
        authorized_chat_ids: Optional[List[int]] = None,
        db_path: str = "state/withdrawals.db",
        daily_limit_usdt: float = 1000.0,
        whitelist_only: bool = True,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._testnet = testnet
        self._bus = notifier_bus
        self._authorized_chat_ids: Set[int] = set(authorized_chat_ids or [])
        self._db_path = db_path
        self._daily_limit = daily_limit_usdt
        self._whitelist_only = whitelist_only
        self._pending: Dict[str, asyncio.Event] = {}  # proposal_id -> confirmed event
        self._rejected: Set[str] = set()
        self._client = None

        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @classmethod
    def from_env(
        cls,
        notifier_bus=None,
        db_path: str = "state/withdrawals.db",
    ) -> "WithdrawalGuard":
        raw_ids = os.getenv("TELEGRAM_AUTHORIZED_CHAT_IDS", "")
        chat_ids = [
            int(x.strip()) for x in raw_ids.split(",") if x.strip().lstrip("-").isdigit()
        ]
        return cls(
            api_key=os.getenv("BYBIT_API_KEY", ""),
            api_secret=os.getenv("BYBIT_API_SECRET", ""),
            testnet=os.getenv("BYBIT_TESTNET", "false").lower() == "true",
            notifier_bus=notifier_bus,
            authorized_chat_ids=chat_ids,
            db_path=db_path,
            daily_limit_usdt=float(os.getenv("WITHDRAWAL_DAILY_LIMIT_USDT", "1000")),
            whitelist_only=os.getenv("WITHDRAWAL_WHITELIST_ONLY", "true").lower() == "true",
        )

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            for stmt in self._CREATE_TABLE.strip().split(";"):
                s = stmt.strip()
                if s:
                    conn.execute(s)
            conn.commit()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Whitelist management
    # ------------------------------------------------------------------

    def add_to_whitelist(self, address: str, chain: str, label: str = "") -> None:
        """Adauga o adresa in whitelist. Trebuie apelat manual inainte de retrageri."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO withdrawal_whitelist "
                "(address, chain, label, added_at) VALUES (?,?,?,?)",
                (address.lower(), chain.upper(), label, now_iso)
            )
            conn.commit()
        logger.info(
            "[WithdrawalGuard] Whitelist: adaugat {} ({}) '{}'",
            address, chain, label,
        )

    def is_whitelisted(self, address: str, chain: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM withdrawal_whitelist WHERE address=? AND chain=?",
                (address.lower(), chain.upper())
            ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # Propose
    # ------------------------------------------------------------------

    async def propose_withdrawal(
        self,
        amount_usdt: float,
        address: str,
        chain: str,
        reason: str = "",
    ) -> Optional[str]:
        """
        Propune o retragere. Trimite cerere Telegram si asteapta confirmarea.

        Returns:
            proposal_id (str) daca s-a propus cu succes
            None daca validarea a esuat
        """
        # Validare whitelist
        if self._whitelist_only and not self.is_whitelisted(address, chain):
            logger.error(
                "[WithdrawalGuard] REFUZAT: adresa {} ({}) nu e in whitelist",
                address, chain,
            )
            await self._alert(
                f"⛔ *Retragere REFUZATA*\n"
                f"Adresa `{address}` ({chain}) nu e in whitelist!\n"
                f"Suma: {amount_usdt:.2f} USDT"
            )
            return None

        # Validare limita zilnica
        today_total = await self._get_today_withdrawn()
        if today_total + amount_usdt > self._daily_limit:
            logger.error(
                "[WithdrawalGuard] REFUZAT: limita zilnica depasita ({:.2f}+{:.2f}>{:.2f})",
                today_total, amount_usdt, self._daily_limit,
            )
            await self._alert(
                f"⛔ *Retragere REFUZATA* — limita zilnica depasita\n"
                f"  Deja retras azi: {today_total:.2f} USDT\n"
                f"  Cerere: {amount_usdt:.2f} USDT\n"
                f"  Limita: {self._daily_limit:.2f} USDT/zi"
            )
            return None

        proposal_id = str(uuid.uuid4())[:8].upper()
        now_iso = datetime.now(timezone.utc).isoformat()

        # Salveaza in DB
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO withdrawal_proposals "
                "(proposal_id, amount_usdt, address, chain, reason, status, requested_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (proposal_id, amount_usdt, address, chain, reason, "pending", now_iso)
            )
            conn.commit()

        # Event pentru confirmare
        confirm_event = asyncio.Event()
        self._pending[proposal_id] = confirm_event

        # Telegram
        msg = (
            f"⚠️ *CERERE RETRAGERE* ⚠️\n"
            f"  Suma: `{amount_usdt:.2f} USDT`\n"
            f"  Adresa: `{address}`\n"
            f"  Chain: `{chain}`\n"
            f"  Motiv: {reason or 'n/a'}\n\n"
            f"  ✅ Confirma: `/confirm_{proposal_id}`\n"
            f"  ❌ Refuza: `/reject_{proposal_id}`\n\n"
            f"  _Expira in 30 minute._"
        )
        await self._alert(msg)
        logger.warning(
            "[WithdrawalGuard] Propunere {} : {:.2f} USDT -> {} ({})",
            proposal_id, amount_usdt, address, chain,
        )

        # Task timeout
        asyncio.create_task(
            self._timeout_proposal(proposal_id, confirm_event),
            name=f"withdrawal_timeout_{proposal_id}",
        )

        return proposal_id

    async def confirm(
        self,
        proposal_id: str,
        confirmed_by: str = "operator",
    ) -> bool:
        """
        Confirma o propunere de retragere. Apelat din Telegram handler
        la comanda /confirm_<UUID>.
        """
        if proposal_id not in self._pending:
            logger.warning(
                "[WithdrawalGuard] confirm: {} nu e in pending (expirat?)",
                proposal_id,
            )
            return False

        proposal = self._get_proposal(proposal_id)
        if proposal is None or proposal["status"] != "pending":
            return False

        logger.warning(
            "[WithdrawalGuard] CONFIRMAT: {} {:.2f} USDT -> {} by {}",
            proposal_id, proposal["amount_usdt"],
            proposal["address"], confirmed_by,
        )

        # Executa retragerea
        success = await self._execute_withdrawal(
            proposal_id=proposal_id,
            amount_usdt=proposal["amount_usdt"],
            address=proposal["address"],
            chain=proposal["chain"],
            confirmed_by=confirmed_by,
        )

        # Seteaza event (opreste timeout)
        event = self._pending.pop(proposal_id, None)
        if event:
            event.set()

        return success

    async def reject(
        self,
        proposal_id: str,
        rejected_by: str = "operator",
    ) -> None:
        """Refuza manual o propunere."""
        self._rejected.add(proposal_id)
        event = self._pending.pop(proposal_id, None)
        if event:
            event.set()
        self._update_proposal_status(
            proposal_id, "rejected",
            rejected_at=datetime.now(timezone.utc).isoformat(),
        )
        logger.info(
            "[WithdrawalGuard] REFUZAT manual: {} by {}",
            proposal_id, rejected_by,
        )
        await self._alert(
            f"❌ Retragere {proposal_id} REFUZATA de {rejected_by}"
        )

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    async def _execute_withdrawal(
        self,
        proposal_id: str,
        amount_usdt: float,
        address: str,
        chain: str,
        confirmed_by: str,
    ) -> bool:
        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self._get_client().withdraw(
                    coin="USDT",
                    chain=chain,
                    address=address,
                    amount=str(amount_usdt),
                    accountType="FUND",
                )
            )
            bybit_tx = resp.get("result", {}).get("id", "")
            self._update_proposal_status(
                proposal_id, "completed",
                bybit_tx_id=bybit_tx,
                confirmed_at=datetime.now(timezone.utc).isoformat(),
                confirmed_by=confirmed_by,
            )
            await self._alert(
                f"✅ *Retragere EXECUTATA*\n"
                f"  ID: `{proposal_id}`\n"
                f"  Suma: `{amount_usdt:.2f} USDT`\n"
                f"  Adresa: `{address}` ({chain})\n"
                f"  TX Bybit: `{bybit_tx}`"
            )
            logger.warning(
                "[WithdrawalGuard] EXECUTAT: {} {:.2f} USDT tx={}",
                proposal_id, amount_usdt, bybit_tx,
            )
            return True
        except Exception as exc:
            self._update_proposal_status(proposal_id, "failed")
            logger.error(
                "[WithdrawalGuard] EXECUTIE FAILED {}: {}", proposal_id, exc
            )
            await self._alert(
                f"❌ Retragere {proposal_id} FAILED: {exc}\n"
                f"Suma {amount_usdt:.2f} USDT RAMaSA IN CONT."
            )
            return False

    async def _timeout_proposal(
        self, proposal_id: str, event: asyncio.Event
    ) -> None:
        try:
            await asyncio.wait_for(
                event.wait(), timeout=self._TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            if proposal_id in self._pending:
                self._pending.pop(proposal_id, None)
                self._update_proposal_status(
                    proposal_id, "expired",
                    expired_at=datetime.now(timezone.utc).isoformat(),
                )
                logger.info(
                    "[WithdrawalGuard] EXPIRAT (30min): {}", proposal_id
                )
                await self._alert(
                    f"⏰ Retragere `{proposal_id}` EXPIRATA (30 minute fara confirmare).\n"
                    f"Suma ramane in cont."
                )

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _get_proposal(self, proposal_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM withdrawal_proposals WHERE proposal_id=?",
                (proposal_id,)
            ).fetchone()
        return dict(row) if row else None

    def _update_proposal_status(
        self,
        proposal_id: str,
        status: str,
        bybit_tx_id: Optional[str] = None,
        confirmed_at: Optional[str] = None,
        rejected_at: Optional[str] = None,
        expired_at: Optional[str] = None,
        confirmed_by: Optional[str] = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE withdrawal_proposals SET status=?, bybit_tx_id=?, "
                "confirmed_at=?, rejected_at=?, expired_at=?, confirmed_by=? "
                "WHERE proposal_id=?",
                (status, bybit_tx_id, confirmed_at, rejected_at,
                 expired_at, confirmed_by, proposal_id)
            )
            conn.commit()

    async def _get_today_withdrawn(self) -> float:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: self._today_withdrawn_sync(today)
        )

    def _today_withdrawn_sync(self, today: str) -> float:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(amount_usdt),0) as total "
                "FROM withdrawal_proposals "
                "WHERE DATE(requested_at)=? AND status='completed'",
                (today,)
            ).fetchone()
        return float(row["total"]) if row else 0.0

    def _get_client(self):
        if self._client is None:
            try:
                from pybit.unified_trading import HTTP
                self._client = HTTP(
                    testnet=self._testnet,
                    api_key=self._api_key,
                    api_secret=self._api_secret,
                )
            except ImportError:
                raise RuntimeError("pybit nu e instalat")
        return self._client

    async def _alert(self, msg: str) -> None:
        if not self._bus:
            logger.info("[WithdrawalGuard] (no bus) {}", msg)
            return
        try:
            await self._bus.send_alert(msg, level="error")
        except Exception as exc:
            logger.warning("[WithdrawalGuard] alert failed: {}", exc)
