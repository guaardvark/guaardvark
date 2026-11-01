"""How an outreach post may reach each platform, declared in one table.

Every draft waits for a person's approval; nothing posts on a grade alone.
What happens after the approval depends on the platform's ``mode``:

``WATCHED``  Reddit, YouTube, Facebook. The agent browser posts there, so an
             approved draft goes out only when a person presses "Post now, I'm
             watching" for it with the Agent Screen open (watched_post). One
             click starts one post; nothing drains a queue of them, on a
             schedule or otherwise. A YouTube account connected for comments
             takes the same click through the official API instead (api_post).
``BOT``      Discord. The Discord bot plugin sends approved replies through
             the bot API, claiming each one through /claim and /submit.
``REFUSED``  X (Twitter) and LinkedIn. Both forbid driving an account with
             browser automation, which is the only way outreach could post
             there, so outreach never does: a draft is refused when it is
             created or approved, and a URL on them is not scouted. A person's
             own LinkedIn posts go through the official API instead, as a
             Social Accounts connection (connections/providers/linkedin.py).

A platform missing from the table (an "internal" note, for one) has no poster;
its approved drafts are left alone. Platform names are compared lowercased.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

WATCHED = "watched"
BOT = "bot"
REFUSED = "refused"


@dataclass(frozen=True)
class PlatformPolicy:
    mode: str
    label: str
    # Actions a poster here handles on the platform; empty when nothing posts.
    actions: tuple[str, ...] = ()
    # Hosts that belong to the platform, for refusing a URL on a REFUSED one.
    hosts: tuple[str, ...] = ()
    # Why nothing posts here, shown wherever a REFUSED draft is turned away.
    refusal: str = ""

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "label": self.label,
            "actions": list(self.actions),
            "refusal": self.refusal or None,
        }


_X_REFUSAL = (
    "Guaardvark does not post to X. X suspends accounts driven by browser "
    "automation, and the agent browser is the only way this install could post there."
)
_LINKEDIN_REFUSAL = (
    "Outreach does not post to LinkedIn. LinkedIn's terms forbid bots and browser "
    "automation on member accounts, so the agent browser never posts or replies "
    "there. To post to your own LinkedIn, connect it under Social Accounts: that "
    "uses LinkedIn's official API, and each post waits for your approval."
)

POLICIES: dict[str, PlatformPolicy] = {
    "reddit": PlatformPolicy(WATCHED, "Reddit", actions=("comment", "share")),
    "youtube": PlatformPolicy(WATCHED, "YouTube", actions=("comment", "reply")),
    "facebook": PlatformPolicy(WATCHED, "Facebook", actions=("comment",)),
    "discord": PlatformPolicy(BOT, "Discord", actions=("comment",)),
    "x": PlatformPolicy(REFUSED, "X", hosts=("x.com", "twitter.com"), refusal=_X_REFUSAL),
    "twitter": PlatformPolicy(REFUSED, "X", hosts=("x.com", "twitter.com"), refusal=_X_REFUSAL),
    "linkedin": PlatformPolicy(REFUSED, "LinkedIn", hosts=("linkedin.com",), refusal=_LINKEDIN_REFUSAL),
}


def _key(platform: Optional[str]) -> str:
    return (platform or "").strip().lower()


def policy_for(platform: Optional[str]) -> Optional[PlatformPolicy]:
    return POLICIES.get(_key(platform))


def label_for(platform: Optional[str]) -> str:
    policy = policy_for(platform)
    return policy.label if policy else (platform or "")


def refusal_for(platform: Optional[str]) -> Optional[str]:
    """Why nothing posts to ``platform``, or None when it is not REFUSED."""
    policy = policy_for(platform)
    return policy.refusal if policy and policy.mode == REFUSED else None


def platforms_in(mode: str) -> tuple[str, ...]:
    return tuple(name for name, policy in POLICIES.items() if policy.mode == mode)


def posts(platform: Optional[str], action: Optional[str]) -> bool:
    """Whether a poster here handles ``action`` on ``platform``."""
    policy = policy_for(platform)
    return bool(policy) and _key(action) in policy.actions


def after_approval(platform: Optional[str]) -> str:
    """What happens to an approved draft on ``platform``, in words for the person."""
    policy = policy_for(platform)
    if policy and policy.mode == WATCHED:
        from backend.services.social_outreach import api_post

        if api_post.platform_ready(platform):
            return "It posts when you press Post now on the Approvals page."
        return (
            "It posts when you press Post now on the Approvals page with the "
            "Agent Screen open."
        )
    if policy and policy.mode == BOT:
        return f"The {policy.label} bot sends it on its next poll."
    return "Nothing here posts it."


def refusal_for_url(url: Optional[str]) -> Optional[str]:
    """The refusal of the REFUSED platform ``url`` is on, or None."""
    from backend.utils.hosts import host_matches

    host = urlparse(url or "").hostname
    for policy in POLICIES.values():
        if policy.mode == REFUSED and host_matches(host, *policy.hosts):
            return policy.refusal
    return None


def to_dict() -> dict:
    """The table as the status route and the pages read it."""
    return {name: policy.to_dict() for name, policy in POLICIES.items()}
