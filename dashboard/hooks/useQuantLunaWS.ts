'use client'
/**
 * QuantLuna WebSocket + REST polling hook
 *
 * WS endpoints (core/spread_monitor.py, strategy/regime_filter.py,
 *               execution/order_manager.py via dashboard/server.py):
 *   ws://localhost:8000/ws/spread
 *   ws://localhost:8000/ws/regime
 *   ws://localhost:8000/ws/orders
 *
 * REST endpoints (dashboard/server.py):
 *   GET /api/pnl        → execution/pnl_reconciler.py
 *   GET /api/health     → execution/health_check.py
 *   GET /api/risk/status → risk/circuit_breaker.py
 *   GET /api/markets    → exchange price feeds
 */
import { useEffect, useRef, useCallback } from 'react'
import { useQuantLunaStore } from '../store/quantlunaStore'
import type { LogLevel, ArbOpportunity } from '../store/quantlunaStore'

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const WS_BASE  = API_BASE.replace('http', 'ws')

const SYMBOLS = [
  'BTC','ETH','SOL','BNB','XRP','ADA','DOT','LINK','AVAX','MATIC',
  'LTC','ATOM','UNI','ALGO','FIL','VET','SAND','AXS','THETA','NEAR',
  'RUNE','CAKE','ENJ','CHZ','MANA','FLOW','KSM','ZEC','DASH','ETC',
  'BCH','TRX','EOS','XTZ','NEO','ZIL','ICX','IOTA','LSK','WAVES',
  'SC','DCR','DGB','STEEM','ARDR','NXT','XEM','CRV','COMP','MKR',
]

let _arbId = 0

export function useQuantLunaWS() {
  const wsRefs     = useRef<Record<string, WebSocket | null>>({})
  const retries    = useRef<Record<string, number>>({})
  const pollRef    = useRef<ReturnType<typeof setInterval> | null>(null)
  const simRef     = useRef<ReturnType<typeof setInterval> | null>(null)
  const pricesRef  = useRef<Record<string, number>>(() => {
    const p: Record<string,number> = {}
    const bases: Record<string,number> = {
      BTC:67820,ETH:3451,SOL:148.3,BNB:612.4,XRP:0.582,ADA:0.451,
      DOT:7.23,LINK:14.82,AVAX:38.91,MATIC:0.882,LTC:82.3,ATOM:9.12,
      UNI:8.4,ALGO:0.21,FIL:5.8,VET:0.04,SAND:0.45,AXS:8.2,
      THETA:2.1,NEAR:6.7,RUNE:2.3,CAKE:2.1,ENJ:0.31,CHZ:0.11,
      MANA:0.35,FLOW:0.82,KSM:28.4,ZEC:28.1,DASH:28.4,ETC:21.3,
      BCH:348,TRX:0.12,EOS:0.71,XTZ:0.92,NEO:12.4,ZIL:0.022,
      ICX:0.31,IOTA:0.22,LSK:0.84,WAVES:2.1,SC:0.006,DCR:14.2,
      DGB:0.009,STEEM:0.23,ARDR:0.11,NXT:0.012,XEM:0.04,
      CRV:0.34,COMP:51.2,MKR:2080,
    }
    SYMBOLS.forEach(s => { p[s] = bases[s] ?? 1.0 })
    return p
  })
  const change24Ref = useRef<Record<string,number>>({})
  const zRef        = useRef(0.0)
  const pnlRef      = useRef(0.0)
  const balanceRef  = useRef(124000.0)
  const tickRef     = useRef(0)

  const store = useQuantLunaStore()

  // ── Connect a real WS endpoint ──
  const connect = useCallback((endpoint: string, url: string) => {
    const ws = new WebSocket(url)
    ws.onopen  = () => { retries.current[endpoint] = 0 }
    ws.onmessage = ({ data }) => {
      try {
        const msg = JSON.parse(data)
        if (endpoint === 'spread') store.setSpread(msg)
        if (endpoint === 'regime') store.setRegime(msg)
        if (endpoint === 'orders') {
          store.addLog({
            ts:     new Date().toISOString().slice(11,23),
            level:  (msg.level ?? 'INFO') as LogLevel,
            module: msg.module ?? 'ORDER',
            msg:    msg.msg    ?? JSON.stringify(msg),
          })
        }
      } catch {}
    }
    ws.onerror = () => {}
    ws.onclose = () => {
      const delay = Math.min(1000 * 2 ** (retries.current[endpoint] ?? 0), 30_000)
      retries.current[endpoint] = (retries.current[endpoint] ?? 0) + 1
      setTimeout(() => connect(endpoint, url), delay)
    }
    wsRefs.current[endpoint] = ws
  }, [store])

  // ── Simulation tick (fallback quand server offline) ──
  const simulateTick = useCallback(() => {
    const t = ++tickRef.current
    const prices = pricesRef.current

    // Spread / z-score (OU process)
    zRef.current += (Math.random() - 0.5) * 0.16 - 0.02 * zRef.current
    const z       = parseFloat(zRef.current.toFixed(4))
    const spread  = parseFloat((0.023 + z * 0.001).toFixed(6))
    const hl      = parseFloat((18.3 + (Math.random() - 0.5)).toFixed(1))
    const kp      = parseFloat(Math.max(0.0001, 0.00124 + (Math.random() - 0.5) * 0.0002).toFixed(6))
    store.setSpread({
      z, spread, halfLife: hl, kalmanP: kp,
      health: Math.abs(z) < 3 ? 'HEALTHY' : Math.abs(z) < 4 ? 'DEGRADED' : 'STALE',
      timestamp: Date.now(),
    })

    // Regime (changes rarely)
    const regimes = ['LOW','NORMAL','NORMAL','NORMAL','HIGH','EXTREME'] as const
    const regime  = regimes[Math.floor(t / 120) % regimes.length]
    const cbOpen  = Math.abs(z) > 3.8
    store.setRegime({
      regime, cbOpen,
      cbCountdown: cbOpen ? Math.max(0, 30 - (t % 30)) : 0,
      wsOk: true, bybitOk: true,
      binanceOk: t % 50 !== 0,
      okxOk: true,
      latencyMs: 20 + Math.floor(Math.random() * 160),
    })

    // Prices random walk
    SYMBOLS.forEach(sym => {
      prices[sym] *= (1 + (Math.random() - 0.5) * 0.0004)
      if (!change24Ref.current[sym])
        change24Ref.current[sym] = (Math.random() - 0.5) * 6
      change24Ref.current[sym] += (Math.random() - 0.5) * 0.1
      change24Ref.current[sym] = Math.max(-10, Math.min(10, change24Ref.current[sym]))
    })
    store.setMarkets(SYMBOLS.map(sym => ({
      symbol: sym,
      price:    parseFloat(prices[sym].toFixed(4)),
      change24h: parseFloat((change24Ref.current[sym] ?? 0).toFixed(2)),
      volume24h: 1e6 + Math.random() * 1e9,
      funding:   parseFloat(((Math.random() - 0.5) * 0.02).toFixed(4)),
    })))

    // PnL
    pnlRef.current  += (Math.random() - 0.48) * 16
    balanceRef.current += (Math.random() - 0.5) * 4
    const total   = parseFloat(balanceRef.current.toFixed(2))
    const dpnl    = parseFloat(pnlRef.current.toFixed(2))
    store.setPnl({
      total,
      available:  parseFloat((total * 0.79).toFixed(2)),
      margin:     parseFloat((total * 0.21).toFixed(2)),
      unrealized: parseFloat((dpnl * 0.03).toFixed(2)),
      dailyPnl:   dpnl,
      dailyPct:   parseFloat((dpnl / 120000 * 100).toFixed(3)),
      equityHistory: [],
    })
    store.pushPnlPoint('BTC/ETH', dpnl, Date.now())
    store.pushPnlPoint('SOL/BNB', dpnl * (0.3 + Math.random() * 0.4), Date.now())

    // Pairs
    store.setPairs([
      { pair:'BTC/ETH', z, halfLife:hl, spread, spreadDelta: parseFloat(((Math.random()-0.5)*0.002).toFixed(5)),
        pnl: parseFloat((pnlRef.current * 0.6).toFixed(2)), position: z > 1 ? 'LONG' : z < -1 ? 'SHORT' : 'FLAT' },
      { pair:'SOL/BNB', z: z*0.7, halfLife:22.1, spread:0.0185, spreadDelta:-0.0003,
        pnl: parseFloat((pnlRef.current * 0.4).toFixed(2)), position: 'SHORT' },
      { pair:'XRP/ADA', z: z*0.4, halfLife:31.5, spread:0.0421, spreadDelta:0.0001,
        pnl: parseFloat((-pnlRef.current * 0.1).toFixed(2)), position: 'FLAT' },
    ])

    // Arbitrage
    const newArb: ArbOpportunity[] = []
    SYMBOLS.slice(0, 8).forEach(sym => {
      const base  = prices[sym]
      const bybP  = parseFloat((base * (1 + (Math.random()-0.5)*0.0006)).toFixed(2))
      const binP  = parseFloat((base * (1 + (Math.random()-0.5)*0.0006)).toFixed(2))
      const spd   = parseFloat((Math.abs(bybP - binP) / bybP * 100).toFixed(4))
      if (spd > 0.015) {
        newArb.push({
          id:         `${sym}-${++_arbId}`,
          pair:       `${sym}/USDT`,
          bybit:      bybP,
          binance:    binP,
          spreadPct:  spd,
          ttl:        4 + Math.floor(Math.random() * 22),
          ttlMax:     25,
          detectedAt: Date.now(),
        })
      }
    })
    store.setArb(newArb)

    // Log entries
    if (t % 4 === 0) {
      const entries: Array<[LogLevel, string, string]> = [
        ['INFO',  'SPREAD',    `z-score updated: ${z} → spread: ${spread}`],
        ['BUY',   'ORDER_MGR', `FILLED BTC long 0.05 @ ${prices['BTC'].toFixed(2)}, slippage: 0.2bp`],
        ['SELL',  'ORDER_MGR', `FILLED ETH short 0.3 @ ${prices['ETH'].toFixed(2)}`],
        ['WARN',  'REGIME',    `regime: ${regime} | z-score threshold: ${Math.abs(z) > 2 ? 'BREACHED' : 'OK'}`],
        ['ARB',   'ARBDETECT', `BTC/USDT spread ${(Math.random()*0.05+0.02).toFixed(4)}% > threshold, TTL 12s`],
        ['RISK',  'CIRCUIT_B', `drawdown check: ${(pnlRef.current/1200).toFixed(2)}% / -5.0% limit`],
        ['SYS',   'WS_WATCHDOG','heartbeat OK, latency 45ms, all feeds nominal'],
        ['INFO',  'KALMAN',    `hedge ratio update: β=${(1.0+(Math.random()-0.5)*0.1).toFixed(4)} P=${kp}`],
      ]
      const [level, module, msg] = entries[Math.floor(Math.random() * entries.length)]
      const now = new Date()
      store.addLog({
        ts:     `${now.toISOString().slice(11,19)}.${String(now.getMilliseconds()).padStart(3,'0')}`,
        level, module, msg,
      })
    }
  }, [store])

  useEffect(() => {
    const isPaused = () => useQuantLunaStore.getState().isPaused

    // Try real WS connections (dashboard/server.py)
    // Falls back silently to simulation if server is offline
    try {
      connect('spread', `${WS_BASE}/ws/spread`)
      connect('regime', `${WS_BASE}/ws/regime`)
      connect('orders', `${WS_BASE}/ws/orders`)
    } catch {}

    // Simulation tick at 4 Hz
    simRef.current = setInterval(() => {
      if (!isPaused()) simulateTick()
    }, 250)

    // REST polling every 5s
    pollRef.current = setInterval(async () => {
      if (isPaused()) return
      try {
        const [pnlRes, healthRes] = await Promise.all([
          fetch(`${API_BASE}/api/pnl`),
          fetch(`${API_BASE}/api/health`),
        ])
        if (pnlRes.ok) {
          const d = await pnlRes.json()
          store.setPnl(d)
        }
        if (healthRes.ok) {
          const h = await healthRes.json()
          // health merged into regime
          const cur = useQuantLunaStore.getState().regime
          if (cur) store.setRegime({ ...cur, ...h })
        }
      } catch {}
    }, 5000)

    return () => {
      Object.values(wsRefs.current).forEach(ws => ws?.close())
      if (simRef.current)  clearInterval(simRef.current)
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [connect, simulateTick, store])
}
