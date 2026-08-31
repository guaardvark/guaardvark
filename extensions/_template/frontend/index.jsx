// What this extension adds to the UI. Every key is optional; see
// frontend/src/extensions.js for the contract. Pages are lazy so nothing
// heavy runs when the app boots.
import React, { lazy } from "react";
import ExtensionIcon from "@mui/icons-material/Extension";

const TemplatePage = lazy(() => import("./TemplatePage"));

export default {
  routes: [{ path: "/template", element: <TemplatePage /> }],
  navGroups: [{ label: "Template", items: [{ text: "Template", icon: <ExtensionIcon />, path: "/template" }] }],
  pageContext: { routeMap: { "/template": { page: "Template", entityType: null } } },
  chatSurfaces: [],
  // themes: { template: { label, description, previewGradient, theme: createFullTheme({...}) } }
  // storeSlice: { state: (set) => ({ templateFlag: false, setTemplateFlag: (v) => set({ templateFlag: v }) }), partialize: ["templateFlag"] },
  // layout: { header: TemplateHeaderBar },
  // logo: TemplateLogo,
};
