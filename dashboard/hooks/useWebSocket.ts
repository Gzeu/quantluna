'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import type { WsMessage, WsStatus } from '../types'

const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? 'ws://localhost:8000/ws/feed'
const HEARTBEAT_INTERVAL_MS = 15_000
const BACKOFF_INITIAL_MS = 1_000
const BACKOFF_MAX_MS = 30_000

interface UseWebSocketReturn {
  lastMessage: WsMessage | null
  status: WsStatus
  send: (msg: unknown) => void
  reconnect: () => void
}

export function useWebSocket(
  onMessage?: (msg: WsMessage) => void,
  url: string = WS_URL,
): UseWebSocketReturn {
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectAttemptRef = useRef(0)
  const heartbeatTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const mountedRef = useRef(true)
  // Store onMessage in a ref so changing the callback never triggers reconnect
  const onMessageRef = useRef(onMessage)
  useEffect(() => { onMessageRef.current = onMessage }, [onMessage])

  const [status, setStatus] = useState<WsStatus>('disconnected')
  const [lastMessage, setLastMessage] = useState<WsMessage | null>(null)

  const clearHeartbeat = () => {
    if (heartbeatTimerRef.current) { clearInterval(heartbeatTimerRef.current); heartbeatTimerRef.current = null }
  }
  const clearReconnect = () => {
    if (reconnectTimerRef.current) { clearTimeout(reconnectTimerRef.current); reconnectTimerRef.current = null }
  }

  const connect = useCallback(() => {
    if (!mountedRef.current) return
    clearHeartbeat()
    clearReconnect()
    if (wsRef.current) { wsRef.current.onclose = null; wsRef.current.close(); wsRef.current = null }
    setStatus('connecting')
    let ws: WebSocket
    try { ws = new WebSocket(url) } catch { setStatus('error'); return }
    wsRef.current = ws

    ws.onopen = () => {
      if (!mountedRef.current) return
      reconnectAttemptRef.current = 0
      setStatus('connected')
      heartbeatTimerRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN)
          ws.send(JSON.stringify({ type: 'ping', payload: null, ts: Date.now() }))
      }, HEARTBEAT_INTERVAL_MS)
    }

    ws.onmessage = (event: MessageEvent) => {
      if (!mountedRef.current) return
      try {
        const msg = JSON.parse(event.data as string) as WsMessage
        if (msg.type === 'pong') return
        setLastMessage(msg)
        onMessageRef.current?.(msg)
      } catch { /* non-JSON — ignore */ }
    }

    ws.onerror = () => { if (mountedRef.current) setStatus('error') }

    ws.onclose = () => {
      if (!mountedRef.current) return
      clearHeartbeat()
      setStatus('disconnected')
      const delay = Math.min(BACKOFF_INITIAL_MS * Math.pow(2, reconnectAttemptRef.current), BACKOFF_MAX_MS)
      reconnectAttemptRef.current += 1
      reconnectTimerRef.current = setTimeout(() => { if (mountedRef.current) connect() }, delay)
    }
  }, [url])

  useEffect(() => {
    mountedRef.current = true
    connect()
    return () => {
      mountedRef.current = false
      clearHeartbeat()
      clearReconnect()
      if (wsRef.current) { wsRef.current.onclose = null; wsRef.current.close(); wsRef.current = null }
    }
  }, [connect])

  const send = useCallback((msg: unknown) => {
    if (wsRef.current?.readyState === WebSocket.OPEN)
      wsRef.current.send(JSON.stringify(msg))
  }, [])

  const reconnect = useCallback(() => { reconnectAttemptRef.current = 0; connect() }, [connect])

  return { lastMessage, status, send, reconnect }
}
