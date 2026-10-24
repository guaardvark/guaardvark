// frontend/src/pages/MCPServersPage.jsx
// MCP Servers page: the local MCP servers the agent can use, reached from
// Settings → Agents.

import React from "react";
import PageLayout from "../components/layout/PageLayout";
import MCPServersSection from "../components/settings/MCPServersSection";
import { useUiTranslation } from "../i18n";

const MCPServersPage = () => {
  const { t } = useUiTranslation();
  return (
    <PageLayout title={t("mcp.title")} variant="standard">
      <MCPServersSection />
    </PageLayout>
  );
};

export default MCPServersPage;
