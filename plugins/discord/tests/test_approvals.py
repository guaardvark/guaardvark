"""Tests for Discord tool-approval views."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from core.approvals import ToolApprovalView, make_approval_handler


@pytest.mark.asyncio
async def test_auto_approve_skips_view():
    send_fn = AsyncMock()
    handler = make_approval_handler(send_fn, user_id=1, auto_approve=True)
    assert await handler({"tools": ["edit_image"]}) is True
    send_fn.assert_not_awaited()


@pytest.mark.asyncio
async def test_approval_view_only_requester_can_click():
    view = ToolApprovalView(user_id=42)
    other = MagicMock()
    other.user = MagicMock()
    other.user.id = 99
    other.response = AsyncMock()
    assert await view.interaction_check(other) is False
    other.response.send_message.assert_awaited()

    owner = MagicMock()
    owner.user = MagicMock()
    owner.user.id = 42
    owner.response = AsyncMock()
    assert await view.interaction_check(owner) is True
