"""Error format for the agent API: {"code": "...", "detail": "..."} (docs/api-contract.yaml)."""
from rest_framework import exceptions
from rest_framework.views import exception_handler


def _flatten(detail):
    """Turn DRF validation errors into one short line. Field names and messages only, never values."""
    if isinstance(detail, dict):
        return "; ".join(f"{field}: {_flatten(message)}" for field, message in detail.items())
    if isinstance(detail, (list, tuple)):
        return " ".join(_flatten(item) for item in detail)
    return str(detail)


def agent_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None
    if isinstance(exc, (exceptions.ValidationError, exceptions.ParseError)):
        code = "invalid_request"
        detail = _flatten(exc.detail)
    elif isinstance(exc, exceptions.Throttled):
        code = "rate_limited"
        detail = "too many attempts, try again later"
    elif isinstance(exc, exceptions.APIException):
        code = exc.get_codes() if isinstance(exc.get_codes(), str) else exc.default_code
        detail = str(exc.detail)
    else:
        code = "error"
        detail = "request failed"
    response.data = {"code": code, "detail": detail}
    return response
