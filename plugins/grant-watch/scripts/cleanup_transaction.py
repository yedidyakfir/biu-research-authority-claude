"""Archive transaction and OS lock contract; no standalone production activation.

The Notion adapter deliberately cannot satisfy atomic_family_compare_exchange.
Tests inject a transactional mock, which is not evidence of native Notion safety.
"""
from contextlib import AbstractContextManager
import copy
import ctypes
from ctypes import wintypes
from datetime import datetime, timedelta, time as daytime
import json
import os
from pathlib import Path
import socket
import time

import cadence_contract as c
import cleanup_contract as cleanup
import notice_contract as n
from publication_adapter import Hold


class SharedRunLock(AbstractContextManager):
    """Windows FileShare.None, held by an open handle; persistent inode/path.

    This implements an interoperable protocol, not a claim that the live worker
    uses it. Binding its exact path/protocol in BOTH workers is a deployment gate.
    Never remove a lock file or decide a live handle is stale based on its age.
    """
    protocol = "windows_file_share_none_v1"

    def __init__(self, path, expected_host):
        self.path, self.expected_host = Path(path), expected_host
        self.handle = None

    def __enter__(self):
        if os.name != "nt" or socket.gethostname().casefold() != self.expected_host.casefold():
            raise Hold("Shared lock requires the verified Windows host")
        if not self.path.is_absolute() or not self.path.parent.is_dir() or self.path.resolve() != self.path.absolute():
            raise Hold("Shared lock path must be absolute, existing-parent and non-redirected")
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        self.api.CreateFileW.restype = wintypes.HANDLE
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        # GENERIC_READ|WRITE, share=0, OPEN_ALWAYS. Do not truncate, write or unlink.
        handle = self.api.CreateFileW(str(self.path), 0xC0000000, 0, None, 4, 0x80, None)
        if handle == wintypes.HANDLE(-1).value:
            raise Hold("Shared grant lock unavailable (Win32 " + str(ctypes.get_last_error()) + "); no overlap")
        self.handle = handle
        return self

    def __exit__(self, *unused):
        if self.handle is not None:
            self.api.CloseHandle(self.handle)
            self.handle = None


def archive(store, lock, journal_dir, *, monotonic=time.monotonic):
    """Requires server-atomic revision over ledger + controls + Runs + config.

    compare_exchange must reject if ANY covered value changed, including another
    child's creation, an operator checkbox, run start, or a configuration edit.
    A single-row precondition and read-after-write are explicitly insufficient.
    """
    base = dict(no_send=True, writes_attempted=0, archived=[], outcomes=[], production_writes=False)
    if not store.atomic_family_compare_exchange or not store.shared_lock_verified:
        return dict(base, status="blocked", reason="Atomic family coordination and deployed shared lock required")
    # In this staged release only the injectable disposable/mock store can reach
    # mutation. There is no ordinary CLI/policy flag that enables production.
    if store.isolation != "disposable_fixture":
        return dict(base, status="blocked", reason="Production activation remains disabled")
    started = monotonic()
    journal_dir = Path(journal_dir)
    journal_dir.mkdir(parents=True, exist_ok=True)
    try:
        with lock:
            initial = store.read()
            cfg = cleanup.settings(initial["snapshot"]["settings"])

            def guard(readback):
                now = store.now()
                local = now.astimezone(c.TZ)
                start = datetime.combine(local.date(), daytime.fromisoformat(cfg["local_time"]), c.TZ)
                if (not start <= local < start + timedelta(minutes=cfg["runtime_minutes"]) or
                        monotonic()-started >= cfg["runtime_minutes"]*60):
                    raise Hold("Cleanup clock/runtime window ended; no further writes")
                if readback.get("active_runs"):
                    raise Hold("Active or uncertain grant run; no archive")
                if readback.get("missing_archive_fields") or readback.get("revision") is None:
                    raise Hold("Archive schema or atomic revision missing")
                if readback["snapshot"]["settings"] != initial["snapshot"]["settings"]:
                    raise Hold("Cleanup configuration changed during transaction")
                result = cleanup.plan(readback["snapshot"])
                if result["status"] != "planned":
                    raise Hold(result["reason"])
                return result

            initial_plan = guard(initial)
            # Also reconcile completed/uncertain journal keys, not only new candidates.
            all_keys = [i["key"] for i in initial_plan["eligible"]] + initial_plan["already_archived"]
            keys = all_keys[:cfg["max_archive_rows"]]
            for key in keys:
                fresh = store.read()
                current_plan = guard(fresh)
                raw = next(r for r in fresh["snapshot"]["native_rows"] if r["Key"] == key)
                notice = n.notices_from_notion([raw])[0]
                expected = cleanup.marker(raw, notice)
                journal_path = journal_dir / (n.digest([fresh["source"], expected["attempt_key"]]) + ".json")
                if journal_path.exists():
                    journal = cleanup.read_snapshot(journal_path)
                    if journal["marker"] != expected or journal["source"] != fresh["source"]:
                        raise Hold("Durable archive intent conflicts with immutable history")
                else:
                    journal = dict(source=fresh["source"], marker=expected, stage="prepared",
                        archive_at=(raw["date:Cleanup archived at:start"] if key in current_plan["already_archived"]
                                    else c.iso(store.now())), native_backup=copy.deepcopy(raw),
                        run_backup=copy.deepcopy(fresh["snapshot"]["runs"][notice["run_key"]]))
                    with journal_path.open("x", encoding="utf-8") as out:
                        json.dump(journal, out, ensure_ascii=True, indent=2)

                def save(stage):
                    journal["stage"] = stage
                    cleanup.atomic_json(journal_path, journal)

                def checked_read():
                    value = store.read()
                    result = guard(value)
                    actual = next(r for r in value["snapshot"]["native_rows"] if r["Key"] == key)
                    if (n.digest(cleanup.immutable(actual)) != expected["immutable_sha256"] or
                            value["snapshot"]["runs"][notice["run_key"]] != journal["run_backup"]):
                        raise Hold("Immutable notice or linked receipt changed; stop without repairing history")
                    return value, result, actual

                def write(readback, properties, stage):
                    if not set(properties) <= cleanup.ARCHIVE_FIELDS:
                        raise Hold("Cleanup write exceeded sidecar field allowlist")
                    guard(readback)
                    save(stage + "_started")  # Persist uncertain intent BEFORE provider action.
                    base["writes_attempted"] += 1
                    store.compare_exchange(readback["revision"], key, properties)

                if key in current_plan["already_archived"]:
                    if (raw.get("Cleanup marker") != n.encode_record(expected) or
                            (journal["stage"] != "prepared" and raw.get("date:Cleanup archived at:start") != journal["archive_at"])):
                        raise Hold("Existing archive does not match durable intent")
                    save("complete")
                    base["outcomes"].append(dict(key=key, status="already_archived_read_back"))
                    continue
                if key not in [i["key"] for i in current_plan["eligible"]]:
                    raise Hold("Publication request or eligibility changed; keep record in place")
                if not raw.get("Cleanup marker"):
                    if journal["stage"] != "prepared":
                        raise Hold("Prior marker outcome uncertain; absent readback cannot authorize retry")
                    write(fresh, {"Cleanup marker": n.encode_record(expected)}, "marker")
                fresh, current_plan, raw = checked_read()
                if raw.get("Cleanup marker") != n.encode_record(expected):
                    raise Hold("Marker write not exactly read back; no archive")
                if key not in [i["key"] for i in current_plan["eligible"]]:
                    raise Hold("New publication request after marker; keep record unarchived")
                if journal["stage"] in {"archive_started", "complete"}:
                    raise Hold("Prior archive outcome uncertain; absent flag cannot authorize retry")
                save("marker_verified")
                write(fresh, {"Cleanup archived": "__YES__", "date:Cleanup archived at:start": journal["archive_at"],
                               "date:Cleanup archived at:is_datetime": 1}, "archive")
                _, after_plan, actual = checked_read()
                if key not in after_plan["already_archived"] or actual["date:Cleanup archived at:start"] != journal["archive_at"]:
                    raise Hold("Archive readback inconclusive; reconcile before retry")
                save("complete")
                base["archived"].append(key)
                base["outcomes"].append(dict(key=key, status="archive_and_history_read_back"))
            return dict(base, status="fixture_transaction_verified", held=initial_plan["held"],
                        deferred=initial_plan["deferred"] + all_keys[cfg["max_archive_rows"]:])
    except (Hold, ValueError, KeyError, TypeError, OSError, StopIteration) as exc:
        return dict(base, status="blocked", reason=str(exc), reconcile_required=True)
