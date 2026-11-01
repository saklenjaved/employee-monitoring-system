import logging

from winagent.logging_setup import RedactingFilter, redact


def test_bearer_token_is_masked():
    assert "abc123" not in redact("Authorization: Bearer abc123")


def test_query_string_and_signature_masked():
    url = "https://bucket.s3.example.invalid/obj.png?X-Amz-Signature=SECRETSIG&X-Amz-Credential=KEY"
    out = redact(f"upload to {url}")
    assert "SECRETSIG" not in out and "X-Amz" not in out
    assert "https://bucket.s3.example.invalid/obj.png" in out


def test_key_value_secrets_masked():
    assert "hunter2" not in redact("password=hunter2 ok")
    assert "xyz" not in redact("token: xyz")


def test_filter_applies_to_record(caplog):
    logger = logging.getLogger("winagent.test")
    logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO, logger="winagent.test"):
        logger.info("calling %s with Bearer SECRET-TOKEN", "https://x.invalid/a?token=ABC")
    assert "SECRET-TOKEN" not in caplog.text
    assert "ABC" not in caplog.text


def test_exception_text_removed_from_record():
    rec = logging.LogRecord("winagent", logging.ERROR, __file__, 1, "failed", (), None)
    try:
        raise OSError("https://secret.invalid/?sig=ZZZ")
    except OSError:
        import sys

        rec.exc_info = sys.exc_info()
    RedactingFilter().filter(rec)
    assert rec.exc_info is None
