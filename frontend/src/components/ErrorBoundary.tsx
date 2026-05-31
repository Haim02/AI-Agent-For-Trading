import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error?: Error;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error) {
    console.error("ErrorBoundary:", error);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex min-h-screen items-center justify-center bg-gray-950 p-6">
          <div className="max-w-md rounded-2xl border border-gray-800 bg-gray-900 p-6 text-center">
            <div className="mb-3 text-4xl">⚠️</div>
            <div className="mb-2 font-bold text-white">שגיאה בטעינת הדף</div>
            <div className="mb-4 text-sm text-gray-400">
              משהו השתבש. נסה לרענן או חזור לדף הבית.
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => window.location.reload()}
                className="flex-1 rounded-xl bg-blue-600 py-2 text-sm font-semibold text-white"
              >
                רענן
              </button>
              <button
                onClick={() => {
                  window.location.href = "/";
                }}
                className="flex-1 rounded-xl bg-gray-800 py-2 text-sm font-semibold text-gray-300"
              >
                דף הבית
              </button>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
