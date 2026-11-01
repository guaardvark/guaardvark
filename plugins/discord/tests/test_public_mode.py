"""Public mode keeps strangers to /imagine, in named channels, without the Cast Library."""
from core import public_mode


def config(mode="1", channels="111, 222"):
    return {"security": {"public_mode": mode, "public_channels": channels}}


def test_off_unless_turned_on():
    assert not public_mode.enabled({"security": {}})
    assert not public_mode.enabled(config(mode=""))
    assert not public_mode.enabled(config(mode="0"))
    assert public_mode.enabled(config(mode="1"))


def test_no_named_channel_means_no_channel():
    assert public_mode.channels(config(channels="")) == set()
    assert public_mode.channels(config()) == {111, 222}


def test_only_imagine_is_offered():
    assert public_mode.COMMANDS == {"imagine"}
    assert public_mode.COGS == ("commands.image",)


def test_refuses_sexual_prompts_and_children():
    assert public_mode.refuses("a nude figure")
    assert public_mode.refuses("TEEN at the beach")
    assert not public_mode.refuses("a lighthouse in a storm")
    assert not public_mode.refuses("Essex countryside at dawn")
