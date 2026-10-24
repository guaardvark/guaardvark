import { CORE_NAV_CATALOG } from "../config/navCatalog";
import { navigationLabel } from "../i18n/navigation";

// pageContext.page is the catalog's English label (the backend is sent it as
// is); the chip shows it the way the sidebar does, in the interface's language.
const shownPage = (page) => {
  const core = CORE_NAV_CATALOG.find((entry) => entry.label === page);
  return core ? navigationLabel(page, core) : page;
};

/**
 * Label for the floating chat's context chip.
 *
 * The id is what the backend resolves the account by, so it always stays in
 * the chip; a name the page supplied goes in front of it so two people with
 * the same name are still told apart by the number.
 */
export const contextChipLabel = (pageContext, entityLabel) => {
  if (!pageContext || pageContext.page === "Unknown" || pageContext.page === "Chat") return null;
  if (!pageContext.entityId) return shownPage(pageContext.page);
  if (entityLabel) return `${entityLabel} · #${pageContext.entityId}`;
  return `${shownPage(pageContext.page)} #${pageContext.entityId}`;
};
