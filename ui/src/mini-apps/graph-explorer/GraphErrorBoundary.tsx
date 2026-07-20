import {
  Component,
  type ErrorInfo,
  type ReactNode,
} from "react";

export interface GraphErrorBoundaryProps {
  children: ReactNode;
  /** Changing this key retires the captured renderer error. */
  resetKey: string | number;
  /** Explicit renderer for the first-class, non-canvas investigation surface. */
  fallback: (error: Error, retry: () => void) => ReactNode;
  onError?: (error: Error, info: ErrorInfo) => void;
  onReset?: () => void;
}

interface GraphErrorBoundaryState {
  error: Error | null;
  resetKey: string | number;
}

/**
 * Isolates only a graph renderer. Consumers should keep navigation, controls
 * and the inspector outside this boundary and supply GraphTableFallback (or an
 * equivalent task table) through `fallback`.
 */
export class GraphErrorBoundary extends Component<
  GraphErrorBoundaryProps,
  GraphErrorBoundaryState
> {
  state: GraphErrorBoundaryState = {
    error: null,
    resetKey: this.props.resetKey,
  };

  static getDerivedStateFromProps(
    props: GraphErrorBoundaryProps,
    state: GraphErrorBoundaryState,
  ): Partial<GraphErrorBoundaryState> | null {
    return props.resetKey !== state.resetKey
      ? { error: null, resetKey: props.resetKey }
      : null;
  }

  static getDerivedStateFromError(error: Error): Partial<GraphErrorBoundaryState> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    this.props.onError?.(error, info);
  }

  private readonly retry = (): void => {
    this.setState({ error: null });
    this.props.onReset?.();
  };

  render(): ReactNode {
    if (this.state.error) {
      return this.props.fallback(this.state.error, this.retry);
    }
    return this.props.children;
  }
}

export default GraphErrorBoundary;
