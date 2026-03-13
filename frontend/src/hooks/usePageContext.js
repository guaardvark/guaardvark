import { useMemo } from "react";
import { useLocation, useParams } from "react-router-dom";

const ROUTE_MAP = {
  "/":                    { page: "Dashboard", entityType: null },
  "/dashboard":           { page: "Dashboard", entityType: null },
  "/chat":                { page: "Chat", entityType: null },
  "/documents":           { page: "Documents", entityType: "document" },
  "/documents/bulk-import": { page: "Bulk Import", entityType: "document" },
  "/tasks":               { page: "Tasks", entityType: "task" },
  "/projects":            { page: "Projects", entityType: "project" },
  "/clients":             { page: "Clients", entityType: "client" },
  "/websites":            { page: "Websites", entityType: "website" },
  "/images":              { page: "Images", entityType: "image" },
  "/images/video":        { page: "Video Generator", entityType: "video" },
  "/batch-images":        { page: "Batch Images", entityType: "image" },
  "/rules":               { page: "Rules", entityType: "rule" },
  "/tools":               { page: "Tools", entityType: "tool" },
  "/agents":              { page: "Agents", entityType: "agent" },
  "/training":            { page: "Training", entityType: "training" },
  "/file-generation":     { page: "File Generation", entityType: null },
  "/settings":            { page: "Settings", entityType: null },
  "/progress-test":       { page: "Progress Test", entityType: null },
  "/dev-tools":           { page: "System Dashboard", entityType: null },
  "/plugins":             { page: "Plugins", entityType: "plugin" },
  "/code-editor":         { page: "Code Editor", entityType: "code" },
  "/upload":              { page: "Upload", entityType: null },
  "/content-library":     { page: "Content Library", entityType: "content" },
  "/wordpress/sites":     { page: "WordPress Sites", entityType: "wordpress" },
  "/wordpress/pages":     { page: "WordPress Pages", entityType: "wordpress" },
};

// Parameterized routes that need regex matching
const PARAM_ROUTES = [
  { pattern: /^\/projects\/([^/]+)$/, page: "Project Detail", entityType: "project", paramName: "projectId" },
  { pattern: /^\/code-editor\/([^/]+)$/, page: "Code Editor", entityType: "code", paramName: "projectId" },
];

export const usePageContext = () => {
  const location = useLocation();
  const { projectId } = useParams();

  return useMemo(() => {
    const pathname = location.pathname;

    // Try exact match first
    const exact = ROUTE_MAP[pathname];
    if (exact) {
      return {
        page: exact.page,
        entityType: exact.entityType,
        entityId: null,
        pathname,
      };
    }

    // Try parameterized routes
    for (const route of PARAM_ROUTES) {
      const match = pathname.match(route.pattern);
      if (match) {
        return {
          page: route.page,
          entityType: route.entityType,
          entityId: projectId || match[1] || null,
          pathname,
        };
      }
    }

    return {
      page: "Unknown",
      entityType: null,
      entityId: null,
      pathname,
    };
  }, [location.pathname, projectId]);
};
