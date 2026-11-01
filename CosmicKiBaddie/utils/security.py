"""
Cosmic ki baddie — Anti-Nuke Sliding Window Tracker & Security Safeguards
Provides false-positive resistant rate tracking, trusted user/role verification,
and protection mode multipliers.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Deque, Dict, Iterable, Tuple


class SlidingWindowTracker:
    """
    Tracks security-sensitive actions per `(guild_id, actor_id, event_type)`
    within a configurable sliding window in seconds.
    Never triggers on a single event unless the threshold is explicitly set and exceeded.
    """

    def __init__(self) -> None:
        self._events: Dict[Tuple[int, int, str], Deque[float]] = defaultdict(deque)

    def record_and_count(
        self, guild_id: int, actor_id: int, event_type: str, window_seconds: int
    ) -> int:
        """Record an event timestamp and return the count of events within `window_seconds`."""
        now = time.monotonic()
        cutoff = now - max(5, window_seconds)
        key = (guild_id, actor_id, event_type)
        bucket = self._events[key]
        bucket.append(now)
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        return len(bucket)

    def reset_actor(self, guild_id: int, actor_id: int) -> None:
        """Clear recorded events for an actor in a guild after remediation."""
        to_delete = [k for k in self._events if k[0] == guild_id and k[1] == actor_id]
        for k in to_delete:
            self._events.pop(k, None)


def adjust_threshold_for_mode(base_threshold: int, protection_mode: str) -> int:
    """
    Adjust detection threshold according to server protection mode:
    - `normal`: uses configured threshold (minimum 2 to prevent single-event false positives)
    - `enhanced`: reduces threshold by ~25% (minimum 2)
    - `emergency`: strict threshold of 2 actions within window
    """
    mode = (protection_mode or "normal").lower()
    if mode == "emergency":
        return 2
    if mode == "enhanced":
        return max(2, int(base_threshold * 0.75))
    return max(2, int(base_threshold))


def is_trusted_actor(
    actor_id: int,
    actor_role_ids: Iterable[int],
    guild_owner_id: int,
    bot_user_id: int,
    trusted_users: Iterable[int],
    trusted_roles: Iterable[int],
) -> bool:
    """
    Check if an actor is exempt from automated anti-nuke punishment.
    Includes safeguards: the Server Owner, the Bot itself, and explicitly trusted users/roles.
    """
    if actor_id in (guild_owner_id, bot_user_id):
        return True
    if actor_id in set(trusted_users):
        return True
    if set(actor_role_ids) & set(trusted_roles):
        return True
    return False
