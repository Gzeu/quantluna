/**
 * ErrorBoundary.tsx — S37 polish
 * React class component — prinde erori de render și afișează fallback elegant.
 * Buton Reset retentează render-ul.
 */
import React from 'react';

interface State { hasError: boolean; message: string; }

export class ErrorBoundary extends React.Component<
  { children: React.ReactNode; fallback?: React.ReactNode },
  State
> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { hasError: false, message: '' };
  }

  static getDerivedStateFromError(err: Error): State {
    return { hasError: true, message: err.message };
  }

  componentDidCatch(err: Error, info: React.ErrorInfo) {
    console.error('[ErrorBoundary]', err, info.componentStack);
  }

  render() {
    if (this.state.hasError) {
      return this.props.fallback ?? (
        <div className="min-h-screen bg-gray-950 flex items-center justify-center px-4">
          <div className="bg-gray-900 rounded-2xl p-8 max-w-md w-full text-center">
            <div className="text-4xl mb-4">⚠️</div>
            <h2 className="text-white text-xl font-bold mb-2">Something went wrong</h2>
            <p className="text-gray-400 text-sm mb-6 font-mono break-words">
              {this.state.message || 'Unexpected render error'}
            </p>
            <button
              onClick={() => this.setState({ hasError: false, message: '' })}
              className="px-5 py-2 bg-cyan-800 hover:bg-cyan-700 text-cyan-100 rounded-xl text-sm font-medium transition-colors"
            >
              Try again
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
