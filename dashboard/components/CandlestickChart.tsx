'use client'
import { useEffect, useRef } from 'react'
import { useQuantLunaStore } from '../store/quantlunaStore'

// TradingView lightweight-charts v4 integration
// npm install lightweight-charts

const TIMEFRAMES = ['1m', '5m', '15m', '1h', '4h', '1D']

function generateMockCandles(symbol: string, count = 200) {
  const base: Record<string, number> = {
    'BTC/USDT': 67800, 'ETH/USDT': 3450, 'SOL/USDT': 148,
    'BNB/USDT': 612, 'DEFAULT': 100,
  }
  let price = base[symbol] ?? base['DEFAULT']
  const now  = Math.floor(Date.now() / 1000)
  const candles = []
  for (let i = count - 1; i >= 0; i--) {
    const change = (Math.random() - 0.5) * price * 0.004
    const open   = price
    const close  = price + change
    const high   = Math.max(open, close) * (1 + Math.random() * 0.002)
    const low    = Math.min(open, close) * (1 - Math.random() * 0.002)
    candles.push({
      time:  now - i * 60,
      open:  parseFloat(open.toFixed(2)),
      high:  parseFloat(high.toFixed(2)),
      low:   parseFloat(low.toFixed(2)),
      close: parseFloat(close.toFixed(2)),
    })
    price = close
  }
  return candles
}

export default function CandlestickChart() {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef     = useRef<any>(null)
  const seriesRef    = useRef<any>(null)
  const maSeriesRef  = useRef<any>(null)
  const { candleSymbol, setCandleSymbol } = useQuantLunaStore()
  const activeTf     = useRef('15m')

  useEffect(() => {
    if (!containerRef.current) return
    let chart: any

    const initChart = async () => {
      try {
        const { createChart, CrosshairMode, LineStyle } = await import('lightweight-charts')
        if (!containerRef.current) return

        chart = createChart(containerRef.current, {
          width:  containerRef.current.clientWidth,
          height: containerRef.current.clientHeight,
          layout: {
            background:  { color: '#0D0D1A' },
            textColor:   '#666688',
            fontFamily:  'JetBrains Mono',
            fontSize:    10,
          },
          grid: {
            vertLines:   { color: '#1A1A3E' },
            horzLines:   { color: '#1A1A3E' },
          },
          crosshair: {
            mode: CrosshairMode.Normal,
            vertLine: { color: '#0088FF', labelBackgroundColor: '#0D0D1A' },
            horzLine: { color: '#0088FF', labelBackgroundColor: '#0D0D1A' },
          },
          rightPriceScale: { borderColor: '#1A1A3E' },
          timeScale: {
            borderColor: '#1A1A3E',
            timeVisible: true,
            secondsVisible: false,
          },
        })
        chartRef.current = chart

        // Candlestick series
        const candleSeries = chart.addCandlestickSeries({
          upColor:          '#00FF88',
          downColor:        '#FF2244',
          borderUpColor:    '#00FF88',
          borderDownColor:  '#FF2244',
          wickUpColor:      '#00FF88',
          wickDownColor:    '#FF2244',
        })
        seriesRef.current = candleSeries

        // Kalman band (μ - upper as violet area series)
        const kalmanUpper = chart.addLineSeries({
          color:         'rgba(136,68,255,0.5)',
          lineWidth:     1,
          lineStyle:     LineStyle.Dashed,
          title:         'Kalman +2σ',
          priceLineVisible: false,
          lastValueVisible: false,
        })
        const kalmanLower = chart.addLineSeries({
          color:         'rgba(136,68,255,0.5)',
          lineWidth:     1,
          lineStyle:     LineStyle.Dashed,
          title:         'Kalman -2σ',
          priceLineVisible: false,
          lastValueVisible: false,
        })
        maSeriesRef.current = { upper: kalmanUpper, lower: kalmanLower }

        // Load data
        const candles = generateMockCandles(candleSymbol)
        candleSeries.setData(candles)

        // Kalman band mock data
        const bandData = candles.map(c => ({
          time: c.time,
          upperVal: c.close * 1.005,
          lowerVal: c.close * 0.995,
        }))
        kalmanUpper.setData(bandData.map(d => ({ time: d.time, value: d.upperVal })))
        kalmanLower.setData(bandData.map(d => ({ time: d.time, value: d.lowerVal })))

        chart.timeScale().fitContent()

        // Resize observer
        const ro = new ResizeObserver(() => {
          if (containerRef.current)
            chart.applyOptions({
              width:  containerRef.current.clientWidth,
              height: containerRef.current.clientHeight,
            })
        })
        if (containerRef.current) ro.observe(containerRef.current)
        return () => ro.disconnect()
      } catch (err) {
        console.warn('[Chart] lightweight-charts not available, showing placeholder', err)
      }
    }

    initChart()
    return () => { if (chart) chart.remove() }
  }, [candleSymbol])

  return (
    <div className="ql-panel flex flex-col overflow-hidden" style={{ minHeight: 0 }}>
      {/* Toolbar */}
      <div
        className="flex items-center gap-2 px-3 shrink-0"
        style={{ height: 32, borderBottom: '1px solid #1A1A3E' }}
      >
        <span className="mono" style={{ color: '#00FF88', fontSize: 10, fontWeight: 700, letterSpacing: 2 }}>
          CHART
        </span>
        <span className="mono" style={{ color: '#E0E0F0', fontSize: 11, fontWeight: 700 }}>
          {candleSymbol}
        </span>
        <span style={{ color: '#8844FF', fontSize: 9 }}>Kalman Band ±2σ</span>
        <div className="flex-1" />
        {TIMEFRAMES.map(tf => (
          <button
            key={tf}
            onClick={() => { activeTf.current = tf }}
            className="mono"
            style={{
              background: activeTf.current === tf ? 'rgba(0,136,255,0.2)' : 'transparent',
              border: `1px solid ${activeTf.current === tf ? '#0088FF' : '#1A1A3E'}`,
              color: activeTf.current === tf ? '#0088FF' : '#666688',
              fontSize: 9, padding: '1px 8px', borderRadius: 2, cursor: 'pointer',
            }}
          >
            {tf}
          </button>
        ))}
      </div>

      {/* Chart container */}
      <div ref={containerRef} className="flex-1" style={{ minHeight: 0, position: 'relative' }} />
    </div>
  )
}
