"""Discord Approve/Deny view for unified-chat tool approval requests."""
import asyncio
import logging

import discord

logger = logging.getLogger("discord_bot")

APPROVAL_TIMEOUT_S = 280


class ToolApprovalView(discord.ui.View):
    """Two-button view; only ``user_id`` may click. Times out as deny."""

    def __init__(self, user_id: int, timeout: float = APPROVAL_TIMEOUT_S):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.approved = False
        self.message = None
        self._done = asyncio.get_running_loop().create_future()

    def _finish(self, approved: bool):
        self.approved = approved
        if not self._done.done():
            self._done.set_result(approved)
        self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Only the person who asked can approve this.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.edit_message(content="Approved.", view=None)
        self._finish(True)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger)
    async def deny(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.edit_message(content="Denied.", view=None)
        self._finish(False)

    async def on_timeout(self):
        self.approved = False
        if not self._done.done():
            self._done.set_result(False)
        if self.message is not None:
            try:
                await self.message.edit(content="Approval timed out.", view=None)
            except Exception:
                pass

    async def wait_for_decision(self) -> bool:
        try:
            return bool(await self._done)
        except Exception:
            return False


def make_approval_handler(send_fn, user_id: int, auto_approve: bool = False):
    """Return ``async (data) -> bool`` for UnifiedChatStreamer.

    ``send_fn`` is ``async (content, *, view=None) -> message``.
    """

    async def handler(data: dict) -> bool:
        if auto_approve:
            return True
        tools = data.get("tools") or []
        tools_str = ", ".join(str(t) for t in tools) if tools else "a tool"
        view = ToolApprovalView(user_id=user_id)
        message = await send_fn(
            f"Guaardvark wants to run **{tools_str}**. Approve?",
            view=view,
        )
        view.message = message
        return await view.wait_for_decision()

    return handler
