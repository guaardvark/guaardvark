/**
 * Shared table sorting utilities.
 * Used by ProjectsPage, ClientPage, RulesPage, WebsitesPage,
 * WordPressSitesPage, and FolderContents.
 */

export function descendingComparator(a, b, orderBy) {
  let valA = a[orderBy];
  let valB = b[orderBy];
  if (orderBy.includes(".")) {
    const parts = orderBy.split(".");
    valA = parts.reduce((obj, part) => obj && obj[part], a);
    valB = parts.reduce((obj, part) => obj && obj[part], b);
  }
  if (valA == null && valB == null) return 0;
  if (valA == null) return 1;
  if (valB == null) return -1;
  if (typeof valA === "string" && typeof valB === "string") {
    valA = valA.toLowerCase();
    valB = valB.toLowerCase();
  }
  if (valB < valA) return -1;
  if (valB > valA) return 1;
  return 0;
}

export function getComparator(order, orderBy) {
  return order === "desc"
    ? (a, b) => descendingComparator(a, b, orderBy)
    : (a, b) => -descendingComparator(a, b, orderBy);
}

export const stableSort = (array, comparator) => {
  const stabilizedThis = array.map((el, index) => [el, index]);
  stabilizedThis.sort((a, b) => {
    const order = comparator(a[0], b[0]);
    if (order !== 0) return order;
    return a[1] - b[1];
  });
  return stabilizedThis.map((el) => el[0]);
};
