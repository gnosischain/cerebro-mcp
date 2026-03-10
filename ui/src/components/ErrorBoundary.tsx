import { Component, type ReactNode, type ErrorInfo } from "react";
import { AlertTriangle } from "lucide-react";

interface Props {
  children: ReactNode;
  fallbackLabel?: string;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[ErrorBoundary]", error, info.componentStack);
  }

  render() {
    if (this.state.hasError) {
      const label = this.props.fallbackLabel ?? "Component";
      return (
        <div
          style={{
            background: "var(--surface)",
            border: "1px solid var(--error)",
            borderRadius: "var(--radius-base)",
            padding: "1.25rem",
            margin: "1rem 0",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "0.5rem",
              marginBottom: "0.5rem",
              color: "var(--error)",
              fontWeight: 600,
              fontSize: "0.875rem",
            }}
          >
            <AlertTriangle size={16} />
            {label} rendering failed
          </div>
          <pre
            style={{
              fontSize: "0.75rem",
              color: "var(--text-secondary)",
              overflow: "auto",
              maxHeight: "120px",
              margin: 0,
              background: "transparent",
              border: "none",
              padding: 0,
            }}
          >
            {this.state.error?.message}
          </pre>
        </div>
      );
    }
    return this.props.children;
  }
}
