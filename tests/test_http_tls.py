"""HTTPS only, certificate verification always on, and a private CA bundle that can be supplied."""
import pytest

from tests.fake_http import ScriptedSession
from winagent.config import ConfigError, load_config
from winagent.http_client import HttpClient, PermanentError

BASE = "https://api.example.invalid/api/agent/v1"


def client(session, ca_bundle=None):
    return HttpClient(BASE, 5, lambda: "token", session=session, ca_bundle=ca_bundle)


def test_a_plain_http_address_is_refused():
    with pytest.raises(ValueError):
        HttpClient("http://api.example.invalid/api/agent/v1", 5, lambda: "t")


def test_verification_is_on_by_default():
    session = ScriptedSession([(200, {})])
    client(session).get_json("config/")
    assert session.calls[0]["verify"] is True


def test_a_private_ca_bundle_is_used_for_verification_not_disabled(tmp_path):
    bundle = tmp_path / "ca.pem"
    bundle.write_text("x")
    session = ScriptedSession([(200, {})])
    client(session, ca_bundle=str(bundle)).get_json("config/")
    assert session.calls[0]["verify"] == str(bundle)


def test_verification_is_never_false(tmp_path):
    session = ScriptedSession([(200, {}), (200, {})])
    api = client(session)
    api.get_json("config/")
    api.post_json("heartbeat/", {})
    assert all(call["verify"] is not False for call in session.calls)


def test_redirects_are_never_followed():
    session = ScriptedSession([(302, {})])
    with pytest.raises(PermanentError) as caught:
        client(session).get_json("config/")
    assert caught.value.code == "http_302"
    assert session.calls[0]["allow_redirects"] is False


def test_an_upload_to_a_plain_http_address_is_refused():
    with pytest.raises(PermanentError):
        client(ScriptedSession()).put_absolute("http://bucket.example.invalid/x", b"", {})


def test_a_missing_ca_bundle_file_stops_the_agent_at_startup(tmp_path):
    config = tmp_path / "agent.toml"
    config.write_text(f'[api]\nbase_url = "{BASE}"\nca_bundle = "{(tmp_path / "missing.pem").as_posix()}"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="ca_bundle"):
        load_config(config)


def test_a_ca_bundle_path_with_forward_slashes_loads(tmp_path):
    bundle = tmp_path / "ca.pem"
    bundle.write_text("x")
    config = tmp_path / "agent.toml"
    config.write_text(f'[api]\nbase_url = "{BASE}"\nca_bundle = "{bundle.as_posix()}"\n', encoding="utf-8")
    assert load_config(config).api.ca_bundle == bundle.as_posix()


def test_a_refused_certificate_is_logged_with_its_reason_but_never_the_url():
    """An installed agent that cannot verify the server must say why, so the cause is visible in agent.log."""
    import requests

    from winagent.http_client import HttpClient, TransientError

    class Refusing:
        def request(self, *args, **kwargs):
            raise requests.exceptions.SSLError(
                "HTTPSConnectionPool(host='secret-host.example', port=8443): Max retries exceeded with url: "
                "/api/agent/v1/pairing/requests/abc/ (Caused by SSLError(SSLCertVerificationError(1, "
                "'[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate (_ssl.c:1032)')))"
            )

    client = HttpClient("https://api.example.invalid/api/agent/v1", 5, lambda: None, session=Refusing())
    try:
        client.post_json("pairing/requests/", {}, auth=False)
    except TransientError as exc:
        assert exc.code == "SSLError"  # the short code sent to the server is unchanged
        assert exc.for_log() == "SSLError: certificate verify failed: self-signed certificate"
        assert "secret-host" not in exc.for_log() and "/api/" not in exc.for_log()
    else:
        raise AssertionError("expected TransientError")
