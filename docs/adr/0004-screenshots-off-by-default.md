# ADR 0004: Screenshots off by default; two switches

Status: accepted.

- Off by default. Capture needs BOTH the local config flag `[screenshots] enabled = true` AND the server policy `screenshots.enabled = true`.
- Interval 600 s by default (allowed 60..3600).
- Order: policy, hours, session, lock, excluded app, then capture. Then redaction, temp file, authorize, direct S3 upload, complete, delete temp file in `finally`.
- Blur is a solid-fill placeholder (TAD 13 open).
