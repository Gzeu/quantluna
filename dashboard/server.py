"""
dashboard/server.py  —  QuantLuna FastAPI Dashboard Server v1.4.4
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

try:
    from core.state_bus import bus
except ImportError:
    from state_bus import bus

logger = logging.getLogger(__name__)

# spot=True => no linear perpetual on Bybit
_SYMBOLS: List[Dict[str, Any]] = [
    {"sym": "BTC",  "spot": False},
    {"sym": "ETH",  "spot": False},
    {"sym": "SOL",  "spot": False},
    {"sym": "BNB",  "spot": False},
    {"sym": "AVAX", "spot": False},
    {"sym": "POL",  "spot": False},
    {"sym": "DOT",  "spot": False},
    {"sym": "ADA",  "spot": False},
    {"sym": "LINK", "spot": False},
    {"sym": "UNI",  "spot": False},
    {"sym": "ATOM", "spot": False},
    {"sym": "NEAR", "spot": False},
    {"sym": "ALGO", "spot": False},
    {"sym": "XRP",  "spot": False},
    {"sym": "LTC",  "spot": False},
    {"sym": "DOGE", "spot": False},
    {"sym": "SHIB", "spot": True},
    {"sym": "ARB",  "spot": False},
    {"sym": "OP",   "spot": False},
    {"sym": "TON",  "spot": True},
]

_live_markets: List[Dict[str, Any]] = []
_live_balance: Dict[str, Any] = {}
_exchange_instance = None


async def _init_exchange():
    global _exchange_instance
    try:
        import ccxt.async_support as ccxt
        exchange_name = os.getenv("EXCHANGE", "bybit").lower()
        api_key    = os.getenv("BYBIT_API_KEY")    or os.getenv("BINANCE_API_KEY")    or ""
        api_secret = os.getenv("BYBIT_API_SECRET") or os.getenv("BINANCE_API_SECRET") or ""
        cls = getattr(ccxt, exchange_name, ccxt.bybit)
        params: Dict[str, Any] = {"enableRateLimit": True}
        if api_key and api_secret:
            params["apiKey"] = api_key
            params["secret"] = api_secret
        _exchange_instance = cls(params)
        logger.info(f"Exchange initialised: {exchange_name} (keys={'yes' if api_key else 'no'})")
    except Exception as exc:
        logger.warning(f"Exchange init failed: {exc}")
        _exchange_instance = None


async def _fetch_ticker_safe(sym: str, is_spot: bool) -> Optional[Dict[str, Any]]:
    if _exchange_instance is None:
        return None
    candidates = [f"{sym}/USDT"]
    if not is_spot:
        candidates = [f"{sym}/USDT:USDT", f"{sym}/USDT"]
    for pair in candidates:
        try:
            t = await _exchange_instance.fetch_ticker(pair)
            if t and t.get("last"):
                return t
        except Exception:
            continue
    return None


async def _fetch_markets():
    global _live_markets
    if _exchange_instance is None:
        return
    try:
        tasks = [_fetch_ticker_safe(s["sym"], s["spot"]) for s in _SYMBOLS]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        markets = []
        for item, t in zip(_SYMBOLS, results):
            if not t or isinstance(t, Exception):
                continue
            sym     = item["sym"]
            last    = float(t.get("last")        or 0)
            change  = float(t.get("percentage")  or 0)
            vol     = float(t.get("quoteVolume") or t.get("baseVolume") or 0)
            funding = 0.0
            if not item["spot"]:
                try:
                    fi = await _exchange_instance.fetch_funding_rate(f"{sym}/USDT:USDT")
                    funding = float(fi.get("fundingRate") or 0)
                except Exception:
                    pass
            markets.append({
                "symbol":      sym,
                "price":       round(last, 8),
                "change24h":   round(change, 4),
                "volume24h":   round(vol, 2),
                "fundingRate": round(funding, 6),
            })
        if markets:
            _live_markets = markets
            logger.info(f"Markets updated: {len(markets)}/{len(_SYMBOLS)} symbols")
    except Exception as exc:
        logger.warning(f"fetch_markets error: {exc}")


async def _fetch_balance():
    global _live_balance
    if _exchange_instance is None:
        return
    api_key = os.getenv("BYBIT_API_KEY") or os.getenv("BINANCE_API_KEY") or ""
    if not api_key:
        return

    bal = None
    # Try UNIFIED first, then CONTRACT
    for account_type in ["UNIFIED", "CONTRACT"]:
        try:
            bal = await _exchange_instance.fetch_balance({"accountType": account_type})
            usdt = bal.get("USDT") or {}
            total = float(usdt.get("total") or bal.get("total", {}).get("USDT") or 0)
            if total > 0 or usdt:
                logger.info(f"Balance fetched via {account_type}: total={total}")
                break
        except Exception as exc:
            logger.warning(f"fetch_balance [{account_type}] error: {exc}")
            bal = None
            continue

    if not bal:
        return

    try:
        usdt  = bal.get("USDT") or {}
        total = float(usdt.get("total") or bal.get("total", {}).get("USDT") or 0)
        free  = float(usdt.get("free")  or bal.get("free",  {}).get("USDT") or 0)
        used  = float(usdt.get("used")  or bal.get("used",  {}).get("USDT") or 0)
        upnl  = 0.0
        try:
            lst = bal.get("info", {}).get("result", {}).get("list", [])
            if lst:
                upnl = float(lst[0].get("totalUnrealisedProfit", 0) or 0)
        except Exception:
            pass
        _live_balance = {
            "totalBalance":     round(total, 4),
            "availableBalance": round(free, 4),
            "marginUsed":       round(used, 4),
            "unrealizedPnl":    round(upnl, 4),
            "realizedPnl":      0.0,
        }
        logger.info(f"Balance updated: {total:.4f} USDT (free={free:.4f})")
    except Exception as exc:
        logger.warning(f"fetch_balance parse error: {exc}")


async def _live_data_loop():
    await _init_exchange()
    while True:
        await asyncio.gather(
            _fetch_markets(),
            _fetch_balance(),
            return_exceptions=True,
        )
        await asyncio.sleep(10)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_live_data_loop())
    logger.info("QuantLuna Dashboard starting — live data loop launched")
    yield
    task.cancel()
    if _exchange_instance:
        try:
            await _exchange_instance.close()
        except Exception:
            pass
    logger.info("QuantLuna Dashboard shutting down")


app = FastAPI(title="QuantLuna Dashboard", version="1.4.4", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_static_dir = os.path.join(os.path.dirname(__file__))
try:
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")
except Exception:
    pass

try:
    from api.backtest import router as backtest_router
    app.include_router(backtest_router)
except ImportError as _e:
    logger.warning(f"Backtest router not mounted: {_e}")


def _balance_response() -> Dict[str, Any]:
    if _live_balance:
        state = bus.snapshot_dict()
        return {
            "totalBalance":     _live_balance["totalBalance"],
            "availableBalance": _live_balance["availableBalance"],
            "unrealizedPnl":    state.get("pnl_usdt", _live_balance["unrealizedPnl"]),
            "realizedPnl":      state.get("realized_pnl", _live_balance["realizedPnl"]),
            "marginUsed":       _live_balance.get("marginUsed", 0.0),
        }
    state = bus.snapshot_dict()
    return {
        "totalBalance":     state.get("equity", 0.0),
        "availableBalance": state.get("available_balance", 0.0),
        "unrealizedPnl":    state.get("pnl_usdt", 0.0),
        "realizedPnl":      state.get("realized_pnl", 0.0),
        "marginUsed":       0.0,
    }


@app.get("/")
async def root() -> HTMLResponse:
    html_path = os.path.join(_static_dir, "index.html")
    try:
        with open(html_path) as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>QuantLuna Dashboard</h1>")

@app.get("/api/status")
async def api_status() -> Dict[str, Any]: return bus.snapshot_dict()

@app.get("/api/positions")
async def api_positions() -> Dict[str, Any]:
    positions = bus.get_positions()
    return {"count": len(positions), "positions": [{"pair": p.pair, "direction": p.direction, "qty_y": p.qty_y, "qty_x": p.qty_x, "notional_usdt": p.notional_usdt, "hedge_ratio": p.hedge_ratio, "entry_ts": p.entry_ts} for p in positions]}

@app.get("/api/performance")
async def api_performance() -> Dict[str, Any]:
    return {"equity_curve": bus.get_equity_curve(), "recent_trades": bus.get_recent_trades()[-50:]}

@app.get("/api/health")
async def api_health() -> Dict[str, Any]:
    state = bus.snapshot_dict()
    status = state.get("status", "UNKNOWN")
    return {
        "status": "ok" if status in ("RUNNING", "IDLE") else "error",
        "trading_status": status, "pnl_usdt": state.get("pnl_usdt", 0.0),
        "drawdown": state.get("drawdown", 0.0), "n_trades": state.get("n_trades", 0),
        "last_update": state.get("last_update"), "exchange_connected": _exchange_instance is not None,
        "markets_cached": len(_live_markets), "balance_live": bool(_live_balance),
    }

@app.get("/api/balance")
async def api_balance() -> Dict[str, Any]: return _balance_response()

@app.get("/api/pairs")
async def api_pairs() -> List[Dict[str, Any]]:
    try:
        positions = bus.get_positions()
        if positions:
            return [{"symbol": p.pair, "zscore": getattr(p, "zscore", 0.0), "spread": getattr(p, "spread", 0.0), "halfLife": getattr(p, "half_life", 0.0), "position": getattr(p, "direction", "FLAT"), "pnl": getattr(p, "pnl", 0.0), "spreadHealth": "HEALTHY"} for p in positions]
    except Exception:
        pass
    return []

@app.get("/api/markets")
async def api_markets() -> List[Dict[str, Any]]: return _live_markets

@app.get("/api/risk")
async def api_risk() -> Dict[str, Any]:
    state = bus.snapshot_dict()
    return {"regime": state.get("volatility_regime", "NORMAL"), "cb_open": state.get("status") in ("HALT", "HARD_STOP"), "cb_cooldown": state.get("cb_cooldown", 0)}

@app.get("/api/log")
async def api_log() -> List[Dict[str, Any]]:
    try:
        trades = bus.get_recent_trades()
        if trades:
            return [{"ts": int(time.time() * 1000) - i * 1000, "level": "BUY" if "BUY" in str(t).upper() else "SELL" if "SELL" in str(t).upper() else "INFO", "module": "Executor", "message": str(t)} for i, t in enumerate(trades[-20:])]
    except Exception:
        pass
    return []

@app.get("/api/optimize/results")
async def api_optimize_results(storage: Optional[str] = Query(default=None), study_name: str = Query(default="quantluna_opt"), top_n: int = Query(default=50, ge=1, le=500)) -> Dict[str, Any]:
    if not storage:
        for default in ["sqlite:///optuna.db", "sqlite:///data/optuna.db"]:
            if os.path.exists(default.replace("sqlite:///", "")):
                storage = default
                break
    if not storage:
        return {"study_name": study_name, "n_trials": 0, "trials": [], "message": "No Optuna storage found."}
    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        study = optuna.load_study(study_name=study_name, storage=storage)
        completed = sorted([t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE], key=lambda t: t.value or 0, reverse=True)
        return {"study_name": study_name, "n_trials": len(study.trials), "best_value": round(study.best_value, 4) if completed else None, "best_params": study.best_params if completed else {}, "trials": [{"number": t.number, "value": t.value, "params": t.params} for t in completed[:top_n]]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


class _WSManager:
    def __init__(self): self.active: List[WebSocket] = []
    async def connect(self, ws: WebSocket):
        await ws.accept(); self.active.append(ws)
    def disconnect(self, ws: WebSocket):
        if ws in self.active: self.active.remove(ws)
    async def broadcast(self, data: Dict):
        dead = []
        for ws in self.active:
            try: await ws.send_json(data)
            except Exception: dead.append(ws)
        for ws in dead: self.active.remove(ws)

_ws_manager = _WSManager()

@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket):
    await _ws_manager.connect(websocket)
    try:
        while True:
            await asyncio.sleep(1.0)
            await websocket.send_json(bus.snapshot_dict())
    except (WebSocketDisconnect, Exception):
        _ws_manager.disconnect(websocket)

@app.websocket("/ws/feed")
async def websocket_feed(websocket: WebSocket):
    await _ws_manager.connect(websocket)
    try:
        while True:
            await asyncio.sleep(2.0)
            now = int(time.time() * 1000)
            state = bus.snapshot_dict()
            bal = _balance_response()
            pairs: List[Dict] = []
            try:
                pairs = [{"symbol": p.pair, "zscore": getattr(p, "zscore", 0.0), "spread": getattr(p, "spread", 0.0), "halfLife": getattr(p, "half_life", 0.0), "position": getattr(p, "direction", "FLAT"), "pnl": getattr(p, "pnl", 0.0), "spreadHealth": "HEALTHY"} for p in bus.get_positions()]
            except Exception:
                pass
            for msg in [
                {"type": "balance", "payload": bal, "ts": now},
                {"type": "pairs",   "payload": pairs, "ts": now},
                {"type": "markets", "payload": _live_markets, "ts": now},
                {"type": "regime",  "payload": {"regime": state.get("volatility_regime", "NORMAL"), "cb_open": state.get("status") in ("HALT", "HARD_STOP"), "cb_cooldown": state.get("cb_cooldown", 0)}, "ts": now},
                {"type": "ws_status", "payload": {"bybit": _exchange_instance is not None, "binance": False, "okx": False}, "ts": now},
            ]:
                await websocket.send_json(msg)
    except WebSocketDisconnect:
        _ws_manager.disconnect(websocket)
    except Exception as exc:
        logger.warning(f"WS /ws/feed error: {exc}")
        _ws_manager.disconnect(websocket)
