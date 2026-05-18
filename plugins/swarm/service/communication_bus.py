"""
Inter-Agent Communication Bus — Real-time state sharing via Redis.

Allows agents working in different worktrees to broadcast events,
architecture decisions, and state updates to their sibling agents.
"""

import json
import logging
import os
import redis
import threading

logger = logging.getLogger("swarm.comm_bus")

class CommunicationBus:
    """
    Lightweight Redis pub/sub wrapper for swarm agents.
    """

    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        try:
            self.redis = redis.from_url(redis_url)
            self.pubsub = self.redis.pubsub()
        except Exception as e:
            logger.error(f"Failed to connect to Redis for CommBus: {e}")
            self.redis = None

    def broadcast(self, swarm_id: str, sender_id: str, event_type: str, data: dict):
        """Broadcast a message to all agents in the swarm."""
        if not self.redis: return
        
        channel = f"swarm:{swarm_id}:bus"
        payload = {
            "sender": sender_id,
            "event_type": event_type,
            "data": data
        }
        self.redis.publish(channel, json.dumps(payload))
        logger.debug(f"Broadcast on {channel}: {event_type} from {sender_id}")

    def get_state(self, swarm_id: str) -> dict:
        """Get the current consolidated state for the swarm."""
        if not self.redis: return {}
        
        state_key = f"swarm:{swarm_id}:state"
        state = self.redis.get(state_key)
        return json.loads(state) if state else {}

    def update_state(self, swarm_id: str, key: str, value: any):
        """Update a shared state variable for the swarm."""
        if not self.redis: return
        
        state_key = f"swarm:{swarm_id}:state"
        with self.redis.lock(f"{state_key}:lock"):
            current = self.get_state(swarm_id)
            current[key] = value
            self.redis.set(state_key, json.dumps(current))
            
        # Also broadcast that the state changed
        self.broadcast(swarm_id, "system", "state_updated", {"key": key, "value": value})
