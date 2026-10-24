import { CORE_NAV_CATALOG, SIDEBAR_GROUPS, WORKSPACES } from "../config/navCatalog";
import { translate } from "./index";

/** Translate core labels while preserving names supplied by brands and extensions. */
export function navigationLabel(label, item) {
  if (item) {
    const core = CORE_NAV_CATALOG.find((entry) =>
      item.id ? entry.id === item.id : entry.path === item.path,
    );
    return core?.label === label
      ? translate(`navigation.items.${core.id}`, { defaultValue: label })
      : label;
  }
  if (SIDEBAR_GROUPS.includes(label)) {
    return translate(`navigation.groups.${label}`, { defaultValue: label });
  }
  const workspace = WORKSPACES.find((entry) => entry.label === label);
  return workspace
    ? translate(`navigation.workspaces.${workspace.id}`, { defaultValue: label })
    : label;
}
