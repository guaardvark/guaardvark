// frontend/src/components/social/oauthCode.js
// The authorization code from what a person pasted after approving access:
// the code itself, or the whole address the browser landed on. An address
// must carry the state this dialog's authorization started with, so a code
// from another attempt (or someone else's link) is not used.
import { translate } from "../../i18n";

/**
 * @param {string} pasted
 * @param {string} [expectedState] the state returned by oauth/start
 * @returns {{ code: string, error: string | null }}
 */
export const codeFromPaste = (pasted, expectedState) => {
  const text = (pasted || "").trim();
  if (!text) return { code: "", error: null };
  if (!/^https?:\/\//i.test(text)) {
    // Some platforms end the redirect with "#_", which a copied code keeps.
    return { code: text.split("#")[0], error: null };
  }
  let url;
  try {
    url = new URL(text);
  } catch {
    return { code: "", error: translate("social.oauthCode.unreadable") };
  }
  const params = url.searchParams;
  const denied = params.get("error");
  if (denied) {
    const reason = params.get("error_description") || denied;
    return { code: "", error: translate("social.oauthCode.denied", { reason }) };
  }
  const code = params.get("code");
  if (!code) return { code: "", error: translate("social.oauthCode.noCode") };
  const state = params.get("state");
  if (expectedState && state !== expectedState) {
    return {
      code: "",
      error: translate("social.oauthCode.otherAttempt"),
    };
  }
  return { code, error: null };
};
