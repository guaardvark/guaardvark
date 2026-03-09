// Global frontend configuration
// Poll interval for the system metrics dashboard in milliseconds.
// Adjustable via the VITE_METRICS_POLL_MS environment variable.
export const METRICS_POLL_INTERVAL_MS =
  Number(import.meta.env.VITE_METRICS_POLL_MS) || 5000;
