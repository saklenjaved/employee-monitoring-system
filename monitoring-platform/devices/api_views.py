"""Agent API: registration, policy, events, screenshots and heartbeat (docs/api-contract.yaml)."""
from rest_framework import exceptions, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from monitoring.ingestion import MAX_EVENTS_PER_BATCH, ingest_events
from screenshots import services as screenshot_services

from .authentication import DeviceAuthentication
from .pairing import create_pairing_request, organization_for_token, poll_pairing, renew_credential
from .serializers import (
    PairingRequestSerializer,
    HeartbeatSerializer,
    RegisterSerializer,
    ScreenshotAuthorizeSerializer,
    ScreenshotCompleteSerializer,
)
from .start import start_instruction
from .services import (
    RegistrationError,
    agent_policy,
    mark_policy_served,
    record_heartbeat,
    register_device,
)
from .throttles import AgentCallThrottle, EventBatchThrottle

MAX_BODY_BYTES = 2 * 1024 * 1024
UTC_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class RegisterView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "agent_register"  # limit set in settings: REST_FRAMEWORK

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            device, credential = register_device(
                data["registration_code"],
                data["device_name"],
                data["os_version"],
                data["agent_version"],
                data.get("architecture", ""),
            )
        except RegistrationError:
            raise exceptions.PermissionDenied("registration code not recognised", code="invalid_registration_code")
        response = Response(
            {
                "device_id": str(device.id),
                "credential": credential,
                "expires_at": device.credential_expires_at.strftime(UTC_FORMAT),
            },
            status=status.HTTP_201_CREATED,
        )
        response["Cache-Control"] = "no-store"  # the credential must not be kept by any cache
        return response


class ConfigView(APIView):
    authentication_classes = [DeviceAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [AgentCallThrottle]

    def get(self, request):
        device = request.auth
        policy = agent_policy(device)
        mark_policy_served(device, policy["policy_version"])
        return Response(policy)


class PayloadTooLarge(exceptions.APIException):
    status_code = 413
    default_detail = "batch or payload too large"
    default_code = "payload_too_large"


class Conflict(exceptions.APIException):
    status_code = 409
    default_detail = "conflict"
    default_code = "conflict"


class EventBatchView(APIView):
    authentication_classes = [DeviceAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [EventBatchThrottle]

    def post(self, request):
        try:
            body_size = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            raise exceptions.ValidationError({"body": "the Content-Length header is not a number"})
        if body_size > MAX_BODY_BYTES:
            raise PayloadTooLarge()
        data = request.data
        if not isinstance(data, dict) or set(data) != {"events"}:
            raise exceptions.ValidationError({"events": "the body must be an object with only an events list"})
        events = data["events"]
        if not isinstance(events, list) or not events:
            raise exceptions.ValidationError({"events": "must be a list of 1 to 500 events"})
        if len(events) > MAX_EVENTS_PER_BATCH:
            raise PayloadTooLarge()
        return Response(ingest_events(request.auth, events))


class ScreenshotAuthorizeView(APIView):
    """Step 1 of a screenshot upload: the server checks the policy again and returns a signed upload link."""

    authentication_classes = [DeviceAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [AgentCallThrottle]

    def post(self, request):
        serializer = ScreenshotAuthorizeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            shot, url, headers, expires_at = screenshot_services.authorize_upload(request.auth, serializer.validated_data)
        except screenshot_services.ScreenshotDenied:
            raise exceptions.PermissionDenied("screenshots are not allowed now", code="policy_denies")
        except screenshot_services.ScreenshotConflict:
            raise Conflict("this screenshot was already finished", code="already_finished")
        except ValueError as error:
            raise exceptions.ValidationError({"captured_at": str(error)})
        response = Response(
            {
                "screenshot_id": str(shot.screenshot_id),
                "upload_url": url,
                "upload_method": "PUT",
                "upload_headers": headers,
                "expires_at": expires_at.strftime(UTC_FORMAT),
            }
        )
        response["Cache-Control"] = "no-store"  # the link must not be kept by any cache
        return response


class ScreenshotCompleteView(APIView):
    """Step 2: the agent reports that the upload finished (or failed)."""

    authentication_classes = [DeviceAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [AgentCallThrottle]

    def post(self, request, screenshot_id):
        serializer = ScreenshotCompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            shot = screenshot_services.complete_upload(
                request.auth, screenshot_id, data["status"], data.get("error_code", "")
            )
        except screenshot_services.ObjectMissing:
            raise Conflict("the upload was not found in storage", code="object_missing")
        if shot is None:
            raise exceptions.NotFound("screenshot not found for this device")
        result = "completed" if shot.status == "uploaded" else "upload_failed"
        return Response({"screenshot_id": str(shot.screenshot_id), "status": result})


class HeartbeatView(APIView):
    authentication_classes = [DeviceAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [AgentCallThrottle]

    def post(self, request):
        device = request.auth
        serializer = HeartbeatSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        record_heartbeat(device, serializer.validated_data)
        current = agent_policy(device)["policy_version"]
        return Response(
            {
                "acknowledged": True,
                "policy_changed": device.policy_version_served != current,
                # Null when the employee has not started monitoring, or the start has run out.
                "start": start_instruction(device),
            }
        )


class PairingRequestView(APIView):
    """A PC with no credential asks to connect. It gets a request id and a secret poll token."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "agent_register"

    def post(self, request):
        serializer = PairingRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        details = dict(serializer.validated_data)
        organization = organization_for_token(details.pop("org_token"))
        if organization is None:
            # The same message for a wrong or retired token, so the reply does not reveal which one it was.
            raise exceptions.ValidationError({"org_token": "invalid"})
        pairing, token = create_pairing_request(details, organization)
        response = Response(
            {
                "request_id": str(pairing.id),
                "poll_token": token,
                "expires_at": pairing.expires_at.strftime(UTC_FORMAT),
            },
            status=status.HTTP_201_CREATED,
        )
        response["Cache-Control"] = "no-store"
        return response


class PairingPollView(APIView):
    """The agent asks whether its PC was approved. The credential comes back once, with the approval."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "agent_pairing_poll"

    def get(self, request, request_id):
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        result = None
        if scheme.lower() == "bearer" and token.strip():
            result = poll_pairing(request_id, token.strip())
        if result is None:
            raise exceptions.NotFound()
        if result["status"] == "approved":
            body = {
                "status": "approved",
                "device_id": result["device_id"],
                "credential": result["credential"],
                "expires_at": result["expires_at"].strftime(UTC_FORMAT),
            }
        else:
            body = {"status": result["status"]}
        response = Response(body)
        response["Cache-Control"] = "no-store"
        return response


class CredentialRenewView(APIView):
    """The agent renews its own credential before it expires. No employee approval is needed."""

    authentication_classes = [DeviceAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [AgentCallThrottle]

    def post(self, request):
        device = request.auth
        credential, expires_at = renew_credential(device)
        response = Response({"credential": credential, "expires_at": expires_at.strftime(UTC_FORMAT)})
        response["Cache-Control"] = "no-store"
        return response
