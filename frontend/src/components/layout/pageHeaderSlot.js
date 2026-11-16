// The page header's slot for controls that belong to the page's content
// rather than to the page: PageLayout provides the element, and a component
// further down (a card board's Layout button) portals into it.

import { createContext, useContext } from "react";

export const PageHeaderSlotContext = createContext(null);

/** The header element to portal into, or null on a page without a header. */
export function usePageHeaderSlot() {
  return useContext(PageHeaderSlotContext);
}
