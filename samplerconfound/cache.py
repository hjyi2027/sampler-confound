"""Content-addressed cache for every provider response.

Every generation — sweep, pilot, every probe — goes through here. A response is
stored under a hash of the exact request body plus a replicate index, and a
repeat of the same (request, replicate) returns the stored response without
touching the network. Two consequences, both load-bearing:

  * an interrupted run of anything resumes for free, at any granularity, without
    each script needing its own checkpoint logic;
  * re-analysis can never re-generate. The analysis scripts read stored records,
    and with SAMPLERCONFOUND_OFFLINE=1 a cache miss is an error rather than an
    API call, so "re-run the probe to regenerate the report" is guaranteed to
    bill nothing.

**The replicate index is part of the key, and this is not optional.** The
distinguishability and determinism probes send the *identical* request N times
on purpose, to sample the output distribution. A cache keyed on the request
alone would answer all N from one stored response and make every model look
perfectly deterministic — it would destroy the thing being measured while
looking like a performance optimisation. The sweep has the same structure: its
replicate dimension is exactly repeated requests at a fixed configuration.

Layout: one file per entry at `cache/<hh>/<hash>.json`, written to a temp file
and renamed, so a kill mid-write leaves either the old state or the new one and
never a torn entry. No index to load, no memory growth, O(1) lookup.

The body is canonicalised before hashing (sorted keys, no whitespace) so that
two dicts with the same content produce the same key regardless of insertion
order. The model name is inside the body, so it is inside the key.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / "cache"
OFFLINE_ENV = "SAMPLERCONFOUND_OFFLINE"


class CacheMiss(RuntimeError):
    """Raised on a miss when offline. Re-analysis must never re-generate."""


def canonical(body: dict) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def request_key(body: dict, replicate: int) -> str:
    """sha256 over the canonical body and the replicate index."""
    h = hashlib.sha256()
    h.update(canonical(body).encode("utf-8"))
    h.update(b"\x00replicate=")
    h.update(str(int(replicate)).encode("ascii"))
    return h.hexdigest()


@dataclass
class Stats:
    hits: int = 0
    misses: int = 0
    stores: int = 0

    def __str__(self) -> str:
        return f"cache: {self.hits} hits, {self.misses} misses, {self.stores} stored"


@dataclass
class ResponseCache:
    directory: Path = DEFAULT_DIR
    offline: bool = field(default_factory=lambda: os.environ.get(OFFLINE_ENV, "") not in ("", "0"))
    stats: Stats = field(default_factory=Stats)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _path(self, key: str) -> Path:
        return self.directory / key[:2] / f"{key}.json"

    def get(self, body: dict, replicate: int) -> dict | None:
        e = self.get_entry(body, replicate)
        return e["response"] if e else None

    def get_entry(self, body: dict, replicate: int) -> dict | None:
        """The whole stored entry: response plus `stored_at`, the moment the
        provider answered. That timestamp is the collection date of the call,
        and it survives any later rewrite of a report."""
        p = self._path(request_key(body, replicate))
        if not p.exists():
            with self._lock:
                self.stats.misses += 1
            return None
        try:
            entry = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # Cannot happen with temp+rename, but never trust a file you did
            # not just write. Treat as a miss; the next store overwrites it.
            with self._lock:
                self.stats.misses += 1
            return None
        with self._lock:
            self.stats.hits += 1
        return entry

    def put(self, body: dict, replicate: int, response: dict, **meta) -> dict:
        """Store a response; return the response now on disk for this key.
        (`put_entry` returns the whole entry, with its stored_at.)

        Usually that is `response`. It is not when another writer — a second
        process sending the same request, which happens by design: the
        determinism probe's T=0 condition and the distinguishability probe's
        tight-temperature arm are byte-identical requests — landed the key
        first. Then theirs is kept and returned, and this one is discarded,
        because the alternative is a run whose data differs from what a replay
        of it would produce. The billed call is lost either way; the
        reproducibility need not be.

        The temp file is unique per writer, and the publish is an exclusive
        link, so two writers cannot tear each other's file.
        """
        return self.put_entry(body, replicate, response, **meta)["response"]

    def put_entry(self, body: dict, replicate: int, response: dict, **meta) -> dict:
        key = request_key(body, replicate)
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "key": key,
            "replicate": int(replicate),
            "body": body,
            "response": response,
            "stored_at": time.time(),
            **meta,
        }
        tmp = p.with_name(f"{key}.{os.getpid()}.{threading.get_ident()}.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(entry, fh, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, p)                      # atomic; fails if p exists
        except FileExistsError:
            existing = self._load(p)
            if existing is not None:
                tmp.unlink()
                return existing
            os.replace(tmp, p)                   # existing was corrupt: overwrite
        else:
            tmp.unlink()
        with self._lock:
            self.stats.stores += 1
        return entry

    def _load(self, p: Path) -> dict | None:
        try:
            e = json.loads(p.read_text(encoding="utf-8"))
            return e if "response" in e else None
        except (json.JSONDecodeError, OSError):
            return None

    def fetch(self, body: dict, replicate: int, send) -> dict:
        """Return the cached response, or call `send(body)` once and store it.

        `send` must return the parsed response dict for a successful call and
        raise for anything else — failures are not cached, so a 429 or a network
        drop is retried on the next attempt rather than remembered as a result.
        """
        return self.fetch_entry(body, replicate, send)["response"]

    def fetch_entry(self, body: dict, replicate: int, send) -> dict:
        """As fetch(), returning the stored entry (with `stored_at`)."""
        hit = self.get_entry(body, replicate)
        if hit is not None:
            return hit
        if self.offline:
            raise CacheMiss(
                f"offline and no cached response for replicate {replicate} of "
                f"{body.get('model')}; re-analysis must not generate. Unset "
                f"{OFFLINE_ENV} to allow API calls."
            )
        response = send(body)
        return self.put_entry(body, replicate, response)

    def count(self) -> int:
        if not self.directory.exists():
            return 0
        return sum(1 for _ in self.directory.rglob("*.json"))
