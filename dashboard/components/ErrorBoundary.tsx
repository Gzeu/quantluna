/**
 * ErrorBoundary.tsx — S37 UI/UX
 * Stack trace collapsible; butoane Try again + Reload page
 */
import React from 'react';

interface State {
  hasError:  boolean;
  message:   string;
  stack:     string;
  showStack: boolean;
}

export class ErrorBoundary extends React.Component<
  { children: React.ReactNode; fallback?: React.ReactNode },
  State
> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { hasError: false, message: '', stack: '', showStack: false };
  }

  static getDerivedStateFromError(err: Error): Partial<State> {
    return { hasError: true, message: err.message, stack: err.stack ?? '' };
  }

  componentDidCatch(err: Error, info: React.ErrorInfo) {
    console.error('[ErrorBoundary]', err, info.componentStack);
  }

  render() {
    if (!this.state.hasError) return this.props.children;
    if (this.props.fallback)  return this.props.fallback;

    return (
      <div
        className="min-h-screen flex items-center justify-center p-6"
        style={{ background: 'var(--bg-base)' }}
      >
        <div className="ql-card ql-card-danger p-8 max-w-lg w-full animate-slide-up">
          <div className="flex items-start gap-4 mb-5">
            <div className="w-10 h-10 rounded-xl bg-red-900/50 flex items-center justify-center shrink-0 text-2xl">
              ⚠
            </div>
            <div>
              <h2 className="text-white font-bold text-lg mb-1">Render Error</h2>
              <p className="text-gray-400 text-sm leading-relaxed mono break-all">
                {this.state.message || 'Unexpected error'}
              </p>
            </div>
          </div>

          {this.state.stack && (
            <div className="mb-5">
              <button
                onClick={() => this.setState(s => ({ showStack: !s.showStack }))}
                className="ql-btn ql-btn-ghost text-xs mb-2"
              >
                {this.state.showStack ? '▲ Hide' : '▼ Show'} stack trace
              </button>
              {this.state.showStack && (
                <pre className="bg-gray-950 rounded-lg p-3 text-[10px] text-gray-500 overflow-auto max-h-48 mono">
                  {this.state.stack}
                </pre>
              )}
            </div>
          )}

          <div className="flex gap-3">
            <button
              onClick={() => this.setState({ hasError: false, message: '', stack: '', showStack: false })}
              className="ql-btn ql-btn-primary"
            >
              Try again
            </button>
            <button
              onClick={() => window.location.reload()}
              className="ql-btn ql-btn-ghost"
            >
              Reload page
            </button>
          </div>
        </div>
      </div>
    );
  }
}
