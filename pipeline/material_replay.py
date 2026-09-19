"""Exact-input replay of a documented material prefix, then injected live I/O.

This adapter does not repair data, authenticate provenance claims, import model
configuration, or approve material quality. A caller supplies explicitly frozen
entries and retains the original failed run and any separately documented repair.
"""
from __future__ import annotations

from copy import deepcopy
import json
from threading import RLock
from typing import Callable

from eval.provenance import digest


VERSION = "material-prefix-replay/v1"


class ReplayMismatchError(ValueError):
    """A prefix identity mismatch permanently blocks this replay instance."""


def _copy(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _identity(step, messages, params):
    if not isinstance(step, str) or not step.strip():
        raise ValueError("Replay step must be nonempty text")
    if not isinstance(messages, list) or not isinstance(params, dict):
        raise ValueError("Replay messages must be a list and params an object")
    request = _copy({"step": step, "messages": messages, "params": params})
    # JSON comparison preserves distinctions Python equality would collapse,
    # such as True versus 1. Object key order is not an input-value change.
    canonical = json.dumps(request, ensure_ascii=False, allow_nan=False,
                           sort_keys=True, separators=(",", ":"))
    return request, canonical


class MaterialReplay:
    """Replay a verified-in-order prefix with no mismatch-to-live fallback.

    entries is a list of {step, messages, params, output, provenance}. Provenance
    must be an explicit object supplied by the caller; it is retained as a claim,
    never manufactured or interpreted as proof of semantic correctness.

    Calls are serial. A replay/audit error latches the instance closed. Provider
    failures also stop this material continuation; retry needs a newly declared
    experiment, not an automatic attempt inside this adapter. real_calls counts
    actual delegate invocations, including failures; replayed_calls counts only
    outputs returned from the recorded prefix.
    """

    def __init__(self, entries, live_call: Callable, record: Callable | None = None):
        if not isinstance(entries, list):
            raise ValueError("Replay entries must be an ordered list")
        if not callable(live_call) or (record is not None and not callable(record)):
            raise ValueError("live_call and any record callback must be callable")
        frozen = _copy(entries)
        identities, signatures = [], set()
        required = {"step", "messages", "params", "output", "provenance"}
        for index, entry in enumerate(frozen):
            if not isinstance(entry, dict) or not required <= entry.keys():
                raise ValueError(f"Replay entry {index} is missing explicit input, output or provenance")
            if not isinstance(entry["provenance"], dict) or not entry["provenance"]:
                raise ValueError(f"Replay entry {index} needs nonempty provenance")
            _, canonical = _identity(entry["step"], entry["messages"], entry["params"])
            if canonical in signatures:
                raise ValueError("Duplicate replay request identity; ambiguous prefix is not accepted")
            signatures.add(canonical)
            identities.append(canonical)
        self._entries, self._identities = frozen, identities
        self._entries_hash = digest(frozen)
        self._live_call, self._record = live_call, record
        self._cursor = self._replayed_calls = self._real_calls = self._mismatches = 0
        self._blocked_reason = None
        self._records = []
        self._lock = RLock()

    def _emit(self, event):
        event = _copy({"version": VERSION, "entries_hash": self._entries_hash, **event})
        self._records.append(deepcopy(event))
        if self._record is not None:
            try:
                self._record(deepcopy(event))
            except Exception:
                self._blocked_reason = "audit_callback_failed"
                raise

    @property
    def records(self):
        with self._lock:
            return deepcopy(self._records)

    def summary(self):
        with self._lock:
            return {"version": VERSION, "entries_hash": self._entries_hash,
                "total": len(self._entries), "consumed": self._cursor,
                "remaining": len(self._entries) - self._cursor,
                "replayed_calls": self._replayed_calls, "real_calls": self._real_calls,
                "mismatch_count": self._mismatches, "blocked": self._blocked_reason is not None,
                "blocked_reason": self._blocked_reason, "records_count": len(self._records),
                "provenance_authority": "caller_supplied_audit_claim_not_semantic_approval"}

    def __call__(self, step, messages, **params):
        with self._lock:
            if self._blocked_reason is not None:
                raise ReplayMismatchError("Replay continuation is blocked: " + self._blocked_reason)
            try:
                request, canonical = _identity(step, messages, params)
            except Exception:
                self._blocked_reason = "invalid_request_identity"
                raise
            request_hash = digest(request)
            if self._cursor < len(self._entries):
                index = self._cursor
                entry = self._entries[index]
                if canonical != self._identities[index]:
                    self._mismatches += 1
                    self._blocked_reason = "prefix_identity_mismatch"
                    expected, _ = _identity(entry["step"], entry["messages"], entry["params"])
                    self._emit({"event": "replay_mismatch", "entry_index": index,
                        "expected_request_hash": digest(expected), "actual_request_hash": request_hash,
                        "fallback_to_live": False})
                    raise ReplayMismatchError(f"Replay input does not match frozen entry {index}; no live fallback")
                # The sink must accept the provenance record before this output
                # is consumed or returned. Callback exceptions cannot become a
                # silent retry or a transition to the live provider.
                self._emit({"event": "replay", "phase": "before_return", "entry_index": index,
                    "step": step, "request_hash": request_hash, "output_hash": digest(entry["output"]),
                    "provenance": deepcopy(entry["provenance"]), "real_provider_call": False})
                output = deepcopy(entry["output"])
                self._cursor += 1
                self._replayed_calls += 1
                return output

            self._emit({"event": "live_started", "step": step, "request_hash": request_hash,
                        "prefix_consumed": self._cursor, "real_call_index": self._real_calls + 1})
            self._real_calls += 1
            try:
                output = self._live_call(request["step"], deepcopy(request["messages"]),
                                         **deepcopy(request["params"]))
            except Exception as exc:
                self._blocked_reason = "live_provider_failed"
                self._emit({"event": "live_failed", "step": step, "request_hash": request_hash,
                            "real_call_index": self._real_calls, "error_type": type(exc).__name__})
                raise
            # Live output is not parsed, repaired or substituted here. The
            # existing material boundary retains and validates its true value.
            self._emit({"event": "live_returned", "step": step, "request_hash": request_hash,
                        "real_call_index": self._real_calls})
            return deepcopy(output)
