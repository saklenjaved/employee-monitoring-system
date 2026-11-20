from rest_framework import serializers

# DRF's own message repeats the value the client sent. Ours does not.
NOT_A_VALID_CHOICE = {"invalid_choice": "not a valid choice"}


class StrictSerializer(serializers.Serializer):
    """Rejects fields that are not in the contract (additionalProperties: false)."""

    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = set(data) - set(self.fields)
            if unknown:
                raise serializers.ValidationError({name: "unknown field" for name in sorted(unknown)})
        return super().to_internal_value(data)


class RegisterSerializer(StrictSerializer):
    registration_code = serializers.CharField(min_length=6, max_length=128)
    device_name = serializers.CharField(max_length=128)
    os_version = serializers.CharField(max_length=64)
    architecture = serializers.ChoiceField(choices=["x64"], required=False, error_messages=NOT_A_VALID_CHOICE)
    agent_version = serializers.CharField(max_length=32)


class ScreenshotAuthorizeSerializer(StrictSerializer):
    screenshot_id = serializers.UUIDField()
    captured_at = serializers.DateTimeField()
    content_type = serializers.ChoiceField(choices=["image/png"], error_messages=NOT_A_VALID_CHOICE)
    byte_size = serializers.IntegerField(min_value=1, max_value=10 * 1024 * 1024)
    sha256 = serializers.RegexField(r"^[a-f0-9]{64}$")
    width = serializers.IntegerField(min_value=1, max_value=20000, required=False)
    height = serializers.IntegerField(min_value=1, max_value=20000, required=False)
    redaction_applied = serializers.BooleanField(required=False)

    def validate_screenshot_id(self, value):
        if value.version != 4:
            raise serializers.ValidationError("must be a UUID4")
        return value


class ScreenshotCompleteSerializer(StrictSerializer):
    status = serializers.ChoiceField(choices=["uploaded", "failed"], error_messages=NOT_A_VALID_CHOICE)
    error_code = serializers.RegexField(r"^[A-Za-z0-9_.-]{0,64}$", required=False, allow_blank=True)


class HeartbeatSerializer(StrictSerializer):
    agent_version = serializers.CharField(max_length=32)
    sent_at = serializers.DateTimeField()
    last_successful_sync_at = serializers.DateTimeField(required=False, allow_null=True)
    session_type = serializers.ChoiceField(
        choices=["console", "rdp", "citrix", "vdi", "unknown"], error_messages=NOT_A_VALID_CHOICE
    )
    queue_depth = serializers.IntegerField(min_value=0)
    # A short code such as "ConnectionError". Letters, digits and . _ - only, so it cannot carry free text.
    last_error_code = serializers.RegexField(
        r"^[A-Za-z0-9_.-]{0,64}$", required=False, allow_null=True, allow_blank=True
    )
    monitoring_state = serializers.ChoiceField(
        choices=[
            "active",
            "awaiting_start",
            "paused_hours",
            "paused_policy",
            "unsupported_session",
            "not_registered",
        ],
        required=False,
        error_messages=NOT_A_VALID_CHOICE,
    )
    # The start request the agent is collecting under. Sent only with monitoring_state "active".
    monitoring_start_id = serializers.UUIDField(required=False, allow_null=True)
    # The focused app and, only when the Admin allows titles, its window title. Never background processes.
    focus_app = serializers.CharField(max_length=128, required=False, allow_blank=True, allow_null=True)
    focus_title = serializers.CharField(max_length=256, required=False, allow_blank=True, allow_null=True)


class PairingRequestSerializer(StrictSerializer):
    # The organization comes from this token, never from a value the PC chooses.
    org_token = serializers.CharField(max_length=128)
    device_name = serializers.CharField(max_length=128)
    os_version = serializers.CharField(max_length=64)
    agent_version = serializers.CharField(max_length=32)
    architecture = serializers.ChoiceField(choices=["x64"], required=False, error_messages=NOT_A_VALID_CHOICE)
