import { useEffect, useRef, useState } from "react";
import axios from "axios";
import { hfUrlClientError } from "../utils/hfUrl";
import { translate } from "../i18n";

/**
 * Shared Look-up state for Add Image/Video Model dialogs.
 * Clears a stale preview when the paste changes.
 */
export default function useHfModelLookup({ endpoint, open }) {
  const [url, setUrl] = useState("");
  const [looking, setLooking] = useState(false);
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState("");
  const previewUrlRef = useRef("");

  useEffect(() => {
    if (!open) {
      setUrl("");
      setPreview(null);
      setError("");
      setLooking(false);
      previewUrlRef.current = "";
    }
  }, [open]);

  const onUrlChange = (next) => {
    setUrl(next);
    if (preview && next.trim() !== previewUrlRef.current) {
      setPreview(null);
    }
  };

  const handleLookup = async () => {
    const clientErr = hfUrlClientError(url);
    if (clientErr) {
      setError(clientErr);
      setPreview(null);
      return;
    }
    setError("");
    setLooking(true);
    try {
      const res = await axios.post(endpoint, { url });
      if (res.data.success) {
        setPreview(res.data.data);
        previewUrlRef.current = url.trim();
      } else {
        setPreview(null);
        setError(res.data.error?.message || res.data.message || translate("messages.hfLookup.failed"));
      }
    } catch (err) {
      setPreview(null);
      setError(err.response?.data?.error?.message || err.message || translate("messages.hfLookup.failed"));
    } finally {
      setLooking(false);
    }
  };

  return {
    url,
    onUrlChange,
    looking,
    preview,
    error,
    setError,
    handleLookup,
  };
}
