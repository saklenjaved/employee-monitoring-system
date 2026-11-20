"""Sync manager (TAD 5.1, 6, 8, 11). Rules: sync-and-idempotency.

- Batches of at most 500 events (config `batch_size` is validated to <= 500).
- Events leave the queue only after the server reports them accepted or duplicate,
  or after it rejects them permanently. A failed batch stays queued and is sent again with the
  SAME event_ids, so the server's duplicate check makes retries safe.
- Transient errors back off exponentially, with a cap, and a bounded number of attempts per flush.
- AuthError stops sync and reports the need for re-registration. It never retries forever.
- Screenshot upload is a separate flow and never blocks this one.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from winagent.api_client import AgentApi
from winagent.events import Event, utc_now
from winagent.http_client import AuthError, PermanentError, TransientError
from winagent.queue import EventQueue

log = logging.getLogger("winagent.sync")

MAX_BATCH = 500


@dataclass
class SyncResult:
    sent: int = 0
    accepted: int = 0
    duplicates: int = 0
    rejected: int = 0
    attempts: int = 0
    auth_failed: bool = False
    gave_up: bool = False
    backoff_seconds: list[float] = field(default_factory=list)


class SyncManager:
    def __init__(
        self,
        api: AgentApi,
        queue: EventQueue,
        batch_size: int = MAX_BATCH,
        max_attempts: int = 5,
        backoff_base_seconds: float = 2.0,
        backoff_cap_seconds: float = 300.0,
        sleep: Callable[[float], None] = lambda seconds: None,
        clock: Callable[[], datetime] = utc_now,
        rng: random.Random | None = None,
    ) -> None:
        if not 1 <= batch_size <= MAX_BATCH:
            raise ValueError("batch_size must be 1..500")
        self._api = api
        self._queue = queue
        self._batch = batch_size
        self._max_attempts = max_attempts
        self._base = backoff_base_seconds
        self._cap = backoff_cap_seconds
        self._sleep = sleep
        self._clock = clock
        self._rng = rng or random.Random()
        self.last_successful_sync_at: datetime | None = None
        self.last_error_code: str | None = None
        self.auth_failed = False

    def enqueue(self, event: Event) -> bool:
        return self._queue.add(event)

    def backoff_for(self, attempt: int) -> float:
        """Exponential backoff: base * 2^attempt, capped, with up to 10% jitter."""
        delay = min(self._cap, self._base * (2**attempt))
        return delay + self._rng.uniform(0, 0.1 * delay)

    def flush(self) -> SyncResult:
        result = SyncResult()
        if self.auth_failed:
            result.auth_failed = True
            return result
        while self._queue.depth() > 0:
            batch = self._queue.peek(self._batch)
            outcome = self._send_with_retry(batch, result)
            if outcome != "ok":
                break
        return result

    def _send_with_retry(self, batch: list[Event], result: SyncResult) -> str:
        for attempt in range(self._max_attempts):
            result.attempts += 1
            try:
                response = self._api.ingest_events(batch)
            except AuthError:
                self.auth_failed = True
                self.last_error_code = "auth_failed"
                result.auth_failed = True
                log.warning("sync stopped: device credential rejected; re-registration required")
                return "auth"
            except TransientError as exc:
                self.last_error_code = exc.code
                if attempt + 1 >= self._max_attempts:
                    result.gave_up = True
                    log.warning("sync gave up for now after %d attempts (%s)", attempt + 1, exc.for_log())
                    return "gave_up"
                delay = self.backoff_for(attempt)
                result.backoff_seconds.append(delay)
                self._sleep(delay)
                continue
            except PermanentError as exc:
                # The batch is not retryable as sent. Drop it so one bad event cannot block the queue forever.
                self.last_error_code = exc.code
                ids = {e.event_id for e in batch}
                self._queue.ack(ids)
                result.rejected += len(ids)
                log.warning("batch dropped after permanent error (%s)", exc.for_log())
                return "dropped"
            return self._apply(batch, response, result)
        return "gave_up"

    def _apply(self, batch: list[Event], response: dict, result: SyncResult) -> str:
        accepted = set(response.get("accepted", []))
        duplicates = set(response.get("duplicates", []))
        rejected = set(response.get("rejected", []))
        known = {e.event_id for e in batch}
        done = (accepted | duplicates | rejected) & known
        # Events the server did not mention stay queued and are retried with the same ids.
        self._queue.ack(done)
        result.sent += len(batch)
        result.accepted += len(accepted & known)
        result.duplicates += len(duplicates & known)
        result.rejected += len(rejected & known)
        if not done:
            # The server acknowledged nothing for this batch. Stop this flush and try again next cycle,
            # so one silent response cannot loop forever.
            self.last_error_code = "no_ack"
            log.warning("batch got no acknowledgement; will retry next sync cycle")
            return "no_progress"
        self.last_successful_sync_at = self._clock()
        self.last_error_code = None
        return "ok"
