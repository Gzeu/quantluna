"""
execution/internal_transfer_manager.py  -  QuantLuna Internal Transfer Manager v1.0

Sprint S32 (2026-07-12):
  Gestioneaza transferuri interne intre wallet-urile Bybit:
    - UNIFIED (Futures/Derivatives) <-> SPOT
  Fara risc de pierdere: banii raman in acelasi cont Bybit.

  API Bybit folosit: POST /v5/asset/transfer/inter-transfer
  Transfer types:
    - futures_to_spot: UNIFIED -> SPOT
    - spot_to_futures: SPOT -> UNIFIED

  Protectii:
    - Cooldown minim intre transferuri (default 10 min)
    - Valoare minima (default 5 USDT)
    - Valoare maxima per transfer (default 10k USDT)
    - Audit log SQLite pentru toate transferurile

Usage::

    mgr = InternalTransferManager.from_env(notifier_bus=bus)
    await mgr.futures_to_spot(amount_usdt=100.0, reason="profit_take")
    await mgr.spot_to_futures(amount_usdt=200.0, reason="rebalance")
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger


class InternalTransferManager:
    """
    Executa si logheaza transferuri interne Bybit Futures <-> Spot.
    """

    _UNIFIED = "UNIFIED"   # wallet Futures/Derivatives
    _SPOT = "SPOT"         # wallet Spot

    _CREATE_TABLE = """
    CREATE TABLE IF NOT EXISTS internal_transfers (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        transfer_id    TEXT NOT NULL UNIQUE,
        from_wallet    TEXT NOT NULL,
        to_wallet      TEXT NOT NULL,
        asset          TEXT NOT NULL DEFAULT 'USDT',
        amount         REAL NOT NULL,
        reason         TEXT NOT NULL DEFAULT '',
        status         TEXT NOT NULL DEFAULT 'pending',
        bybit_tx_id    TEXT,
        created_at     TEXT NOT NULL,
        completed_at   TEXT
    );
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = False,
        notifier_bus=None,
        db_path: str = "state/internal_transfers.db",
        cooldown_seconds: int = 600,    # 10 minute intre transferuri
        min_amount_usdt: float = 5.0,
        max_amount_usdt: float = 10_000.0,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._testnet = testnet
        self._bus = notifier_bus
        self._db_path = db_path
        self._cooldown_s = cooldown_seconds
        self._min_amount = min_amount_usdt
        self._max_amount = max_amount_usdt
        self._last_transfer_ts: float = 0.0
        self._client = None

        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @classmethod
    def from_env(
        cls,
        notifier_bus=None,
        db_path: str = "state/internal_transfers.db",
    ) -> "InternalTransferManager":
        return cls(
            api_key=os.getenv("BYBIT_API_KEY", ""),
            api_secret=os.getenv("BYBIT_API_SECRET", ""),
            testnet=os.getenv("BYBIT_TESTNET", "false").lower() == "true",
            notifier_bus=notifier_bus,
            db_path=db_path,
        )

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(self._CREATE_TABLE)
            conn.commit()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def futures_to_spot(
        self,
        amount_usdt: float,
        asset: str = "USDT",
        reason: str = "manual",
    ) -> bool:
        """Transfera USDT din Futures (UNIFIED) in Spot wallet."""
        return await self._transfer(
            from_wallet=self._UNIFIED,
            to_wallet=self._SPOT,
            amount=amount_usdt,
            asset=asset,
            reason=reason,
        )

    async def spot_to_futures(
        self,
        amount_usdt: float,
        asset: str = "USDT",
        reason: str = "manual",
    ) -> bool:
        """Transfera USDT din Spot in Futures (UNIFIED) wallet."""
        return await self._transfer(
            from_wallet=self._SPOT,
            to_wallet=self._UNIFIED,
            amount=amount_usdt,
            asset=asset,
            reason=reason,
        )

    # ------------------------------------------------------------------
    # Core transfer logic
    # ------------------------------------------------------------------

    async def _transfer(
        self,
        from_wallet: str,
        to_wallet: str,
        amount: float,
        asset: str,
        reason: str,
    ) -> bool:
        import time

        # Validari
        if amount < self._min_amount:
            logger.warning(
                "[InternalTransfer] Amount {:.2f} < min {:.2f} — skip",
                amount, self._min_amount,
            )
            return False

        if amount > self._max_amount:
            logger.warning(
                "[InternalTransfer] Amount {:.2f} > max {:.2f} — skip",
                amount, self._max_amount,
            )
            return False

        # Cooldown
        elapsed = time.monotonic() - self._last_transfer_ts
        if elapsed < self._cooldown_s:
            remaining = self._cooldown_s - elapsed
            logger.warning(
                "[InternalTransfer] Cooldown activ: mai asteapta {:.0f}s",
                remaining,
            )
            return False

        transfer_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()

        # Log pending
        self._log_transfer(
            transfer_id=transfer_id,
            from_wallet=from_wallet,
            to_wallet=to_wallet,
            asset=asset,
            amount=amount,
            reason=reason,
            status="pending",
            created_at=now_iso,
        )

        logger.info(
            "[InternalTransfer] {} -> {} : {:.2f} {} ({})",
            from_wallet, to_wallet, amount, asset, reason,
        )

        # Executa
        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self._get_client().create_internal_transfer(
                    transferId=transfer_id,
                    coin=asset,
                    amount=str(amount),
                    fromAccountType=from_wallet,
                    toAccountType=to_wallet,
                )
            )
            bybit_tx = resp.get("result", {}).get("transferId", transfer_id)
            self._update_transfer_status(
                transfer_id, "completed",
                bybit_tx_id=bybit_tx,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            self._last_transfer_ts = time.monotonic()

            msg = (
                f"\u21c4 *Transfer intern OK*\n"
                f"  `{from_wallet}` \u2192 `{to_wallet}`\n"
                f"  Suma: `{amount:.2f} {asset}`\n"
                f"  Motiv: {reason}\n"
                f"  TX: `{bybit_tx}`"
            )
            await self._alert(msg)
            logger.info(
                "[InternalTransfer] OK: {} -> {} {:.2f} {} tx={}",
                from_wallet, to_wallet, amount, asset, bybit_tx,
            )
            return True

        except Exception as exc:
            self._update_transfer_status(transfer_id, "failed")
            logger.error(
                "[InternalTransfer] FAILED {} -> {} {:.2f} {}: {}",
                from_wallet, to_wallet, amount, asset, exc,
            )
            await self._alert(
                f"❌ Transfer intern FAILED\n"
                f"  {from_wallet} \u2192 {to_wallet} {amount:.2f} {asset}\n"
                f"  Eroare: {exc}"
            )
            return False

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _log_transfer(
        self, transfer_id: str, from_wallet: str, to_wallet: str,
        asset: str, amount: float, reason: str, status: str, created_at: str,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO internal_transfers "
                "(transfer_id, from_wallet, to_wallet, asset, amount, "
                " reason, status, created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (transfer_id, from_wallet, to_wallet, asset,
                 amount, reason, status, created_at)
            )
            conn.commit()

    def _update_transfer_status(
        self,
        transfer_id: str,
        status: str,
        bybit_tx_id: Optional[str] = None,
        completed_at: Optional[str] = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE internal_transfers SET status=?, bybit_tx_id=?, "
                "completed_at=? WHERE transfer_id=?",
                (status, bybit_tx_id, completed_at, transfer_id)
            )
            conn.commit()

    async def _alert(self, msg: str) -> None:
        if not self._bus:
            return
        try:
            await self._bus.send_alert(msg, level="info")
        except Exception as exc:
            logger.warning("[InternalTransfer] alert failed: {}", exc)
