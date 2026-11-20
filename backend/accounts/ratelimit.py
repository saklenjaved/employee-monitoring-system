"""Small cache-based limits for web login and password reset.

The limits are defaults to confirm. The cache is per process in development;
production needs a shared cache so every worker sees the same counters.
"""
import hashlib

from django.core.cache import cache


def client_ip(request):
    return request.META.get("REMOTE_ADDR", "unknown")


def key_for(prefix, value):
    """Cache key for a value such as an email address. The value itself is hashed."""
    return f"{prefix}:{hashlib.sha256(value.strip().lower().encode('utf-8')).hexdigest()}"


def too_many(key, limit):
    return cache.get(key, 0) >= limit


def hit(key, window_seconds):
    """Count one event. The counter disappears after `window_seconds`."""
    if cache.add(key, 1, window_seconds):
        return
    try:
        cache.incr(key)
    except ValueError:  # the counter expired between the two calls
        cache.set(key, 1, window_seconds)
