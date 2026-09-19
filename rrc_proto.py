# RRC wire constants and the envelope layer, split out of the session the
# way rnsh_proto.py is split out of rnsh_client.py.
#
# Wire protocol constants (RRC_VERSION, envelope keys, message types, HELLO/WELCOME
# body keys, WELCOME limits keys) are copied from rrcd/constants.py and are
# authoritative for on-wire compatibility. Local defaults (ENVELOPE_OVERHEAD,
# DEFAULT_MAX_BODY, DEFAULT_MAX_NICK) and the ERR_* strings are not in rrcd/constants.py
# — they come from rrcd/config.py defaults and the hub's wire behaviour.
# See FR-rrc-client.md for the source citations. Nothing here touches the radio,
# so it is all host-testable.

import os
import time

import rrc_cbor as cbor

RRC_VERSION = 1
HUB_APP = "rrc"
HUB_ASPECT = "hub"

# Envelope keys
K_V = 0
K_T = 1
K_ID = 2
K_TS = 3
K_SRC = 4
K_ROOM = 5
K_BODY = 6
K_NICK = 7
K_DST = 8

# Message types
T_HELLO = 1
T_WELCOME = 2
T_JOIN = 10
T_JOINED = 11
T_PART = 12
T_PARTED = 13
T_MSG = 20
T_NOTICE = 21
T_ACTION = 22
T_PING = 30
T_PONG = 31
T_ERROR = 40
T_RESOURCE_ENVELOPE = 50

# HELLO / WELCOME body keys
B_HELLO_NAME = 0
B_HELLO_VER = 1
B_HELLO_CAPS = 2
B_WELCOME_HUB = 0
B_WELCOME_VER = 1
B_WELCOME_CAPS = 2
B_WELCOME_LIMITS = 3

# WELCOME limits map keys
L_MAX_NICK = 0
L_MAX_ROOM = 1
L_MAX_BODY = 2
L_MAX_ROOMS = 3
L_RATE = 4

# Worst-case bytes an envelope spends on everything but the body:
# map header 1, ver 2, type 2, msg_id 10, ts 10, src 18, room ~8,
# body header 3, nick ~10.
ENVELOPE_OVERHEAD = 64
DEFAULT_MAX_BODY = 350
DEFAULT_MAX_NICK = 32

# The two hub ERROR strings that drive behaviour rather than display.
ERR_BANNED = "banned"
ERR_RATE_LIMITED = "rate limited"

_EPOCH_OFFSET = 946684800   # MicroPython counts from 2000-01-01, Unix from 1970


def now_ms(epoch_s=None):
    """Epoch milliseconds, or 0 when the mesh clock has not synced.

    K_TS is required and validated as an unsigned int, but rrcd never
    reads the value, so 0 is honest and safe.

    The device needs two checks, not one. ui._clock_valid uses
    time.localtime()[0] >= 2024 to decide whether the RTC has been set
    from the mesh, and MicroPython's time.time() counts from 2000-01-01,
    so a synced device reads ~8.4e8 where Unix time reads ~1.76e9.
    Testing the raw seconds against a Unix threshold alone would report
    0 forever on real hardware."""
    if epoch_s is None:
        try:
            if time.localtime()[0] < 2024:
                return 0
            epoch_s = time.time()
        except Exception:
            return 0
        if epoch_s < 1700000000:
            epoch_s += _EPOCH_OFFSET
    if epoch_s < 1700000000:
        return 0
    return int(epoch_s * 1000)


def normalize_nick(value, max_bytes=DEFAULT_MAX_NICK):
    """Trim, strip LF/CR/NUL, truncate to a UTF-8 byte budget.

    Mirrors rrcd/util.py normalize_nick, except that it truncates where
    the hub rejects: the node display name is not ours to refuse. The budget
    arrives from a hub's WELCOME limits map as untrusted CBOR: it can be
    negative, or not an integer at all. Either way we send no nick rather than
    looping forever on a budget that can never be met -- a nickless member is
    valid, a hung device is not."""
    if not value:
        return None
    if not isinstance(max_bytes, int) or max_bytes <= 0:
        return None
    s = value.strip()
    for bad in ("\n", "\r", "\x00"):
        s = s.replace(bad, "")
    if not s:
        return None
    while len(s.encode("utf-8")) > max_bytes:
        s = s[:-1]
    return s or None


def body_cap(mdu, hub_limit=None):
    """Longest message body that fits both the hub's rule and the link.

    The hub's 350-byte default leaves ~17 bytes of headroom at MTU 500,
    and a path that negotiates lower inverts that, so this is computed
    per session and never hardcoded."""
    limit = hub_limit if hub_limit else DEFAULT_MAX_BODY
    return max(1, min(limit, mdu - ENVELOPE_OVERHEAD))


def make_envelope(t, src, room=None, body=None, nick=None, ts=None, mid=None):
    env = {
        K_V: RRC_VERSION,
        K_T: t,
        K_ID: mid if mid else os.urandom(8),
        K_TS: now_ms() if ts is None else ts,
        K_SRC: src,
    }
    if room is not None:
        env[K_ROOM] = room
    if body is not None:
        env[K_BODY] = body
    if nick:
        env[K_NICK] = nick
    return env


def parse(data):
    """Decode an inbound packet into an envelope, or None if unusable.

    Deliberately permissive about extra keys: hubs relay extension keys
    (>= 64) verbatim, and rejecting an envelope for carrying one would
    lose real messages."""
    try:
        env = cbor.loads(data)
    except Exception:
        return None
    if not isinstance(env, dict):
        return None
    if env.get(K_V) != RRC_VERSION:
        return None
    if not isinstance(env.get(K_T), int):
        return None
    return env
