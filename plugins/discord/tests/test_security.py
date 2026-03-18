"""Tests for security utilities."""
from unittest.mock import MagicMock
from core.security import sanitize_input, is_admin, is_channel_allowed


class TestSanitizeInput:
    """Tests for sanitize_input."""

    def test_strips_mentions(self):
        result = sanitize_input("Hello <@123456> and <@!789012>")
        assert "<@" not in result
        assert "Hello" in result

    def test_strips_everyone_here(self):
        result = sanitize_input("Hey @everyone check @here")
        assert "@everyone" not in result
        assert "@here" not in result

    def test_truncates_long_input(self):
        long_text = "a" * 3000
        result = sanitize_input(long_text, max_length=100)
        assert len(result) == 100

    def test_strips_code_blocks(self):
        result = sanitize_input("before ```python\nprint('hi')\n``` after")
        assert "```" not in result
        assert "print" not in result
        assert "before" in result
        assert "after" in result

    def test_empty_after_sanitization(self):
        result = sanitize_input("<@123456>")
        assert result is None

    def test_normal_text_passes(self):
        result = sanitize_input("Hello world, how are you?")
        assert result == "Hello world, how are you?"

    def test_empty_input(self):
        result = sanitize_input("")
        assert result is None


class TestIsAdmin:
    """Tests for is_admin."""

    def test_admin_role_match(self):
        member = MagicMock()
        role = MagicMock()
        role.name = "Admin"
        member.roles = [role]
        assert is_admin(member, ["Admin", "Bot Admin"]) is True

    def test_no_admin_role(self):
        member = MagicMock()
        role = MagicMock()
        role.name = "Member"
        member.roles = [role]
        assert is_admin(member, ["Admin"]) is False

    def test_none_member(self):
        assert is_admin(None, ["Admin"]) is False


class TestIsChannelAllowed:
    """Tests for is_channel_allowed."""

    def test_empty_allowlist_allows_all(self):
        assert is_channel_allowed(12345, []) is True

    def test_channel_in_allowlist(self):
        assert is_channel_allowed(100, [100, 200, 300]) is True

    def test_channel_not_in_allowlist(self):
        assert is_channel_allowed(999, [100, 200, 300]) is False
