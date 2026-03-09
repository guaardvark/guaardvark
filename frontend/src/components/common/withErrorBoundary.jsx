import React from "react";
import ErrorBoundary from "./ErrorBoundary";

// Higher-order component that wraps components with error boundary
const withErrorBoundary = (Component) => {
  const WrappedComponent = React.forwardRef((props, ref) => (
    <ErrorBoundary>
      <Component {...props} ref={ref} />
    </ErrorBoundary>
  ));

  WrappedComponent.displayName = `withErrorBoundary(${Component.displayName || Component.name})`;
  
  return WrappedComponent;
};

export default withErrorBoundary; 