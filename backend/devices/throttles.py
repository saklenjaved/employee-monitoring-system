"""Limits for the agent API, counted per device (not per IP address, which many devices may share).

The rates are in settings (REST_FRAMEWORK, DEFAULT_THROTTLE_RATES). The counters live in the cache,
which is per process in development; production needs a shared cache (default, to confirm).
"""
from rest_framework.throttling import SimpleRateThrottle


class DeviceRateThrottle(SimpleRateThrottle):
    def get_cache_key(self, request, view):
        device = request.auth
        if device is None:
            return None
        return self.cache_format % {"scope": self.scope, "ident": str(device.pk)}


class EventBatchThrottle(DeviceRateThrottle):
    scope = "agent_events"


class AgentCallThrottle(DeviceRateThrottle):
    scope = "agent_calls"
