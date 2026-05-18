// frontend/src/components/filesystem/BreadcrumbNav.jsx
// Ubuntu-style breadcrumb navigation for file system

import React from "react";
import { Box, Breadcrumbs, Link, Typography } from "@mui/material";
import {
  NavigateNext as NavigateNextIcon,
} from "@mui/icons-material";

const BreadcrumbNav = ({ currentPath, onNavigate }) => {
  const pathParts = currentPath ? currentPath.split("/").filter(Boolean) : [];
  
  const handleClick = (index) => {
    if (index === -1) {
      // Navigate to root
      onNavigate("/");
    } else {
      // Navigate to specific folder - add leading slash to match DB storage format
      const newPath = "/" + pathParts.slice(0, index + 1).join("/");
      onNavigate(newPath);
    }
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
            Home
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


