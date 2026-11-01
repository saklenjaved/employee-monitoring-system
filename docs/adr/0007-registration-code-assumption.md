# ADR 0007: Registration by registration code (ASSUMPTION)

Status: assumed for POC. TAD 7 says the final onboarding design is open.

- The employee enters an organization-issued code. The agent sends it with device name, OS version and agent version to `POST /devices/register/` over TLS.
- The server returns a device id and credential. The agent never sends organization or employee ids.
- One device per employee (POC default). A second registration is the server's decision.
