// frontend/src/components/filesystem/BreadcrumbNav.jsx
// Ubuntu-style breadcrumb navigation for file system

import React from "react";
import { Box, Breadcrumbs, Link, Typography } from "@mui/material";
import {
  NavigateNext as NavigateNextIcon,
} from "@mui/icons-material";
import { useUiTranslation } from "../../i18n";

const normalizePath = (path) => {
  if (!path || path === "/") return "/";
  return `/${String(path).split("/").filter(Boolean).join("/")}`;
};

const BreadcrumbNav = ({ currentPath, onNavigate, rootPath = "/" }) => {
  const { t } = useUiTranslation();
  const root = normalizePath(rootPath);
  const current = normalizePath(currentPath);
  const rootParts = root === "/" ? [] : root.split("/").filter(Boolean);
  const currentParts = current === "/" ? [] : current.split("/").filter(Boolean);
  const pathParts = currentParts.slice(rootParts.length);

  const handleClick = (index) => {
    if (index === -1) {
      onNavigate(root);
      return;
    }
    const newPath = "/" + [...rootParts, ...pathParts.slice(0, index + 1)].join("/");
    onNavigate(newPath);
  };

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        py: 1,
        px: 2,
        borderBottom: 1,
        borderColor: "divider",
        bgcolor: "background.paper",
        minHeight: 48,
      }}
    >
      <Breadcrumbs
        separator={<NavigateNextIcon fontSize="small" sx={{ color: "text.secondary" }} />}
        sx={{ flexGrow: 1 }}
      >
        {/* Home / Root */}
        <Link
          component="button"
          variant="body2"
          onClick={() => handleClick(-1)}
          sx={{
            cursor: "pointer",
            textDecoration: "none",
            color: pathParts.length === 0 ? "primary.main" : "text.primary",
            fontWeight: pathParts.length === 0 ? 600 : 400,
            "&:hover": {
              textDecoration: "underline",
              color: "primary.main",
            },
          }}
        >
          <Typography variant="body2" component="span">
            {t("files.breadcrumb.home")}
          </Typography>
        </Link>

        {/* Path parts */}
        {pathParts.map((part, index) => {
          const isLast = index === pathParts.length - 1;
          
          return isLast ? (
            <Typography
              key={index}
              variant="body2"
              sx={{
                color: "primary.main",
                fontWeight: 600,
              }}
            >
              {part}
            </Typography>
          ) : (
            <Link
              key={index}
              component="button"
              variant="body2"
              onClick={() => handleClick(index)}
              sx={{
                cursor: "pointer",
                textDecoration: "none",
                color: "text.primary",
                "&:hover": {
                  textDecoration: "underline",
                  color: "primary.main",
                },
              }}
            >
              <Typography variant="body2" component="span">
                {part}
              </Typography>
            </Link>
          );
        })}
      </Breadcrumbs>
    </Box>
  );
};

export default BreadcrumbNav;


