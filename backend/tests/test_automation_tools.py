#!/usr/bin/env python3

import os
import sys
import unittest
from unittest.mock import MagicMock, patch, AsyncMock

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

os.environ["GUAARDVARK_MODE"] = "test"
os.environ["GUAARDVARK_BROWSER_AUTOMATION"] = "true"
os.environ["GUAARDVARK_DESKTOP_AUTOMATION"] = "true"
os.environ["GUAARDVARK_GUI_AUTOMATION"] = "false"
os.environ["GUAARDVARK_MCP_ENABLED"] = "true"


class TestBrowserAutomationService(unittest.TestCase):

    def setUp(self):
        from backend.services import browser_automation_service

        browser_automation_service.BrowserAutomationService._instance = None

    def test_singleton_pattern(self):
        from backend.services.browser_automation_service import BrowserAutomationService

        service1 = BrowserAutomationService.get_instance()
        service2 = BrowserAutomationService.get_instance()

        self.assertIs(service1, service2)

    def test_get_state_initial(self):
        from backend.services.browser_automation_service import BrowserAutomationService

        service = BrowserAutomationService.get_instance()
        state = service.get_state()

        self.assertIn("initialized", state)
        self.assertIn("active_pages", state)
        self.assertIn("max_pages", state)
        self.assertEqual(state["active_pages"], 0)

    def test_browser_enabled_check(self):
        from backend.services.browser_automation_service import (
            BROWSER_AUTOMATION_ENABLED,
        )

        self.assertTrue(BROWSER_AUTOMATION_ENABLED)


class TestBrowserTools(unittest.TestCase):

    def test_browser_navigate_tool_exists(self):
        from backend.tools.browser_tools import BrowserNavigateTool

        tool = BrowserNavigateTool()
        self.assertEqual(tool.name, "browser_navigate")
        self.assertIn("url", tool.parameters)

    def test_browser_screenshot_tool_exists(self):
        from backend.tools.browser_tools import BrowserScreenshotTool

        tool = BrowserScreenshotTool()
        self.assertEqual(tool.name, "browser_screenshot")
        self.assertIn("url", tool.parameters)
        self.assertIn("full_page", tool.parameters)

    def test_browser_extract_tool_exists(self):
        from backend.tools.browser_tools import BrowserExtractTool

        tool = BrowserExtractTool()
        self.assertEqual(tool.name, "browser_extract")
        self.assertIn("selector", tool.parameters)

    def test_all_browser_tools_importable(self):
        from backend.tools.browser_tools import (
            BrowserNavigateTool,
            BrowserClickTool,
            BrowserFillTool,
            BrowserScreenshotTool,
            BrowserExtractTool,
            BrowserWaitTool,
            BrowserExecuteJSTool,
            BrowserGetHTMLTool,
        )

        tools = [
            BrowserNavigateTool(),
            BrowserClickTool(),
            BrowserFillTool(),
            BrowserScreenshotTool(),
            BrowserExtractTool(),
            BrowserWaitTool(),
            BrowserExecuteJSTool(),
            BrowserGetHTMLTool(),
        ]

        self.assertEqual(len(tools), 8)
        for tool in tools:
            self.assertTrue(hasattr(tool, "name"))
            self.assertTrue(hasattr(tool, "execute"))


class TestDesktopAutomationService(unittest.TestCase):

    def setUp(self):
        from backend.services import desktop_automation_service

        desktop_automation_service.DesktopAutomationService._instance = None

    def test_singleton_pattern(self):
        from backend.services.desktop_automation_service import DesktopAutomationService

        service1 = DesktopAutomationService.get_instance()
        service2 = DesktopAutomationService.get_instance()

        self.assertIs(service1, service2)

    def test_get_state_initial(self):
        from backend.services.desktop_automation_service import DesktopAutomationService

        service = DesktopAutomationService.get_instance()
        state = service.get_state()

        self.assertIn("desktop_automation_enabled", state)
        self.assertIn("gui_automation_enabled", state)
        self.assertIn("allowed_paths", state)
        self.assertIn("file_watchers_active", state)

    def test_path_validation(self):
        from backend.services.desktop_automation_service import DesktopAutomationService

        service = DesktopAutomationService.get_instance()

        self.assertTrue(service._check_path_allowed("/tmp/test.txt"))

        self.assertFalse(service._check_path_allowed("/etc/passwd"))

    def test_app_validation(self):
        from backend.services.desktop_automation_service import DesktopAutomationService

        service = DesktopAutomationService.get_instance()

        self.assertTrue(service._check_app_allowed("firefox"))

        self.assertFalse(service._check_app_allowed("rm"))


class TestDesktopTools(unittest.TestCase):

    def test_file_watch_tool_exists(self):
        from backend.tools.desktop_tools import FileWatchTool

        tool = FileWatchTool()
        self.assertEqual(tool.name, "file_watch")
        self.assertIn("path", tool.parameters)

    def test_clipboard_tools_exist(self):
        from backend.tools.desktop_tools import ClipboardGetTool, ClipboardSetTool

        get_tool = ClipboardGetTool()
        set_tool = ClipboardSetTool()

        self.assertEqual(get_tool.name, "clipboard_get")
        self.assertEqual(set_tool.name, "clipboard_set")

    def test_notification_tool_exists(self):
        from backend.tools.desktop_tools import NotificationSendTool

        tool = NotificationSendTool()
        self.assertEqual(tool.name, "notification_send")
        self.assertIn("title", tool.parameters)
        self.assertIn("message", tool.parameters)

    def test_all_desktop_tools_importable(self):
        from backend.tools.desktop_tools import (
            FileWatchTool,
            FileBulkOperationTool,
            AppLaunchTool,
            AppListTool,
            AppFocusTool,
            GUIClickTool,
            GUITypeTool,
            GUIHotkeyTool,
            GUIScreenshotTool,
            GUILocateImageTool,
            ClipboardGetTool,
            ClipboardSetTool,
            NotificationSendTool,
        )

        tools = [
            FileWatchTool(),
            FileBulkOperationTool(),
            AppLaunchTool(),
            AppListTool(),
            AppFocusTool(),
            GUIClickTool(),
            GUITypeTool(),
            GUIHotkeyTool(),
            GUIScreenshotTool(),
            GUILocateImageTool(),
            ClipboardGetTool(),
            ClipboardSetTool(),
            NotificationSendTool(),
        ]

        self.assertEqual(len(tools), 13)
        for tool in tools:
            self.assertTrue(hasattr(tool, "name"))
            self.assertTrue(hasattr(tool, "execute"))


class TestMCPClientService(unittest.TestCase):

    def setUp(self):
        from backend.services import mcp_client_service

        mcp_client_service.MCPClientService._instance = None

    def test_singleton_pattern(self):
        from backend.services.mcp_client_service import MCPClientService

        service1 = MCPClientService.get_instance()
        service2 = MCPClientService.get_instance()

        self.assertIs(service1, service2)

    def test_get_state_initial(self):
        from backend.services.mcp_client_service import MCPClientService

        service = MCPClientService.get_instance()
        state = service.get_state()

        self.assertIn("mcp_enabled", state)
        self.assertIn("servers_configured", state)
        self.assertIn("servers_connected", state)
        self.assertIn("total_tools_available", state)

    def test_list_configured_servers(self):
        from backend.services.mcp_client_service import MCPClientService

        service = MCPClientService.get_instance()
        result = service.list_configured_servers()

        self.assertTrue(result.get("success"))
        self.assertIn("servers", result)
        self.assertIn("total", result)


class TestMCPTools(unittest.TestCase):

    def test_mcp_list_servers_tool_exists(self):
        from backend.tools.mcp_tools import MCPListServersTool

        tool = MCPListServersTool()
        self.assertEqual(tool.name, "mcp_list_servers")

    def test_mcp_connect_tool_exists(self):
        from backend.tools.mcp_tools import MCPConnectTool

        tool = MCPConnectTool()
        self.assertEqual(tool.name, "mcp_connect")
        self.assertIn("server", tool.parameters)

    def test_mcp_execute_tool_exists(self):
        from backend.tools.mcp_tools import MCPExecuteTool

        tool = MCPExecuteTool()
        self.assertEqual(tool.name, "mcp_execute")
        self.assertIn("server", tool.parameters)
        self.assertIn("tool", tool.parameters)

    def test_all_mcp_tools_importable(self):
        from backend.tools.mcp_tools import (
            MCPListServersTool,
            MCPConnectTool,
            MCPDisconnectTool,
            MCPListToolsTool,
            MCPExecuteTool,
            MCPGetStateTool,
        )

        tools = [
            MCPListServersTool(),
            MCPConnectTool(),
            MCPDisconnectTool(),
            MCPListToolsTool(),
            MCPExecuteTool(),
            MCPGetStateTool(),
        ]

        self.assertEqual(len(tools), 6)
        for tool in tools:
            self.assertTrue(hasattr(tool, "name"))
            self.assertTrue(hasattr(tool, "execute"))


class TestToolRegistration(unittest.TestCase):

    def test_browser_tools_registration(self):
        from backend.tools.tool_registry_init import register_browser_tools

        registered = register_browser_tools()

        self.assertIn("browser_navigate", registered)
        self.assertIn("browser_screenshot", registered)
        self.assertIn("browser_extract", registered)
        self.assertEqual(len(registered), 8)

    def test_desktop_tools_registration(self):
        from backend.tools.tool_registry_init import register_desktop_tools

        registered = register_desktop_tools()

        self.assertIn("file_watch", registered)
        self.assertIn("clipboard_get", registered)
        self.assertIn("notification_send", registered)
        self.assertEqual(len(registered), 13)

    def test_mcp_tools_registration(self):
        from backend.tools.tool_registry_init import register_mcp_tools

        registered = register_mcp_tools()

        self.assertIn("mcp_list_servers", registered)
        self.assertIn("mcp_execute", registered)
        self.assertEqual(len(registered), 6)


class TestAgentConfigurations(unittest.TestCase):

    def test_browser_automation_agent_exists(self):
        from backend.services.agent_config import DEFAULT_AGENTS

        self.assertIn("browser_automation", DEFAULT_AGENTS)
        agent = DEFAULT_AGENTS["browser_automation"]

        self.assertEqual(agent.name, "Browser Automation Agent")
        self.assertIn("browser_navigate", agent.tools)
        self.assertIn("browser_screenshot", agent.tools)

    def test_desktop_automation_agent_exists(self):
        from backend.services.agent_config import DEFAULT_AGENTS

        self.assertIn("desktop_automation", DEFAULT_AGENTS)
        agent = DEFAULT_AGENTS["desktop_automation"]

        self.assertEqual(agent.name, "Desktop Automation Agent")
        self.assertIn("file_watch", agent.tools)
        self.assertIn("clipboard_get", agent.tools)
        self.assertIn("notification_send", agent.tools)


if __name__ == "__main__":
    unittest.main()
