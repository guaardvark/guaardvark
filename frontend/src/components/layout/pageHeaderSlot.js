// The page header's slots for content that belongs to what is on the page
// rather than to the page: PageLayout provides the elements, and components
// further down portal into them. `status` sits where the title is (and takes
// its place), `actions` sits with the header's buttons, and `primary` holds
// the page's main action a little to their left.

import { createContext, useContext } from "react";

export const PageHeaderSlotContext = createContext(null);

/** The header element to portal into, or null on a page without a header. */
export function usePageHeaderSlot(name = "actions") {
  return useContext(PageHeaderSlotContext)?.[name] || null;
}
