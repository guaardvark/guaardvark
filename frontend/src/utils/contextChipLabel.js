/**
 * Label for the floating chat's context chip.
 *
 * The id is what the backend resolves the account by, so it always stays in
 * the chip; a name the page supplied goes in front of it so two people with
 * the same name are still told apart by the number.
 */
export const contextChipLabel = (pageContext, entityLabel) => {
  if (!pageContext || pageContext.page === "Unknown" || pageContext.page === "Chat") return null;
  if (!pageContext.entityId) return pageContext.page;
  if (entityLabel) return `${entityLabel} · #${pageContext.entityId}`;
  return `${pageContext.page} #${pageContext.entityId}`;
};
