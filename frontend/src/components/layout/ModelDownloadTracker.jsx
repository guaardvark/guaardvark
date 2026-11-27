// frontend/src/components/layout/ModelDownloadTracker.jsx
// Mounted once inside the snackbar provider. Hands the store the app's toast
// and reads every downloader once, so a download that was running before this
// page loaded shows in the footer and is announced when it ends.
import { useEffect } from "react";
import { useSnackbar } from "../common/SnackbarProvider";
import { useModelDownloadsStore } from "../../stores/useModelDownloadsStore";

const ModelDownloadTracker = () => {
  const { showMessage } = useSnackbar();
  const setNotifier = useModelDownloadsStore((s) => s.setNotifier);
  const refreshAll = useModelDownloadsStore((s) => s.refreshAll);

  useEffect(() => {
    setNotifier(showMessage);
    return () => setNotifier(null);
  }, [setNotifier, showMessage]);

  useEffect(() => {
    refreshAll();
  }, [refreshAll]);

  return null;
};

export default ModelDownloadTracker;
