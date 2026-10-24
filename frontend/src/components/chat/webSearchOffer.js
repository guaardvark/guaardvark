/**
 * A reply whose question looked like it needed current information, and that
 * no search ran for, carries web_search_offer: {action, query} (the backend's
 * offer_web_search). Live turns get it from chat:complete; history rows from
 * extra_data.web_search_offer. Nothing is sent unless the person clicks.
 */

import { translate } from "../../i18n";

export const WEB_SEARCH_OFFER_SEARCH = "search";
export const WEB_SEARCH_OFFER_ENABLE = "enable_web_access";

export const searchLabel = () => translate("chat.webSearchOffer.search");
export const searchTooltip = () => translate("chat.webSearchOffer.searchTooltip");
export const searchSentTooltip = () => translate("chat.webSearchOffer.searchSent");
export const enableLabel = () => translate("chat.webSearchOffer.enable");
export const enableHint = () => translate("chat.webSearchOffer.enableHint");

/** The reply's offer as {action, query}, or null when it carries none. */
export function webSearchOfferOf(message) {
  if (!message || message.role !== "assistant") return null;
  const offer = message.web_search_offer ?? message.extra_data?.web_search_offer;
  if (!offer || typeof offer !== "object") return null;
  const query = typeof offer.query === "string" ? offer.query.trim() : "";
  if (!query) return null;
  if (offer.action !== WEB_SEARCH_OFFER_SEARCH && offer.action !== WEB_SEARCH_OFFER_ENABLE) return null;
  return { action: offer.action, query };
}

/**
 * The chat send that takes the offer: one web_search of the person's own
 * question, the same send `/websearch <query>` makes.
 */
export function webSearchSend(query) {
  return {
    text: `/websearch ${query}`,
    options: {
      direct_tool: "web_search",
      direct_tool_params: { query },
      slash_command: "websearch",
      slash_args: query,
    },
  };
}
