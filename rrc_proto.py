# RRC wire constants and the envelope layer, split out of the session the
# way rnsh_proto.py is split out of rnsh_client.py.
#
# Where each value in this file comes from:
#
# - Wire protocol constants (RRC_VERSION, envelope keys, message types,
#   HELLO/WELCOME body keys, WELCOME limits keys) are copied from
#   rrcd/constants.py and are authoritative for on-wire compatibility.
# - Local defaults (DEFAULT_MAX_BODY, DEFAULT_MAX_NICK) and the ERR_*
#   strings are not in rrcd/constants.py — they come from rrcd/config.py
#   defaults and from the hub's wire behaviour.
# - Envelope overhead is NOT a constant here and must not become one. It is
#   measured per session by envelope_overhead(), which encodes this
#   session's own envelope with an empty body; body_cap() sizes the
#   composer from that and encode_capped() measures the real packet before
#   it goes on air. The constant this replaced (ENVELOPE_OVERHEAD = 64,
#   taken from the spec) was arithmetic on a guess, and it overran the link
#   MDU by 2 bytes on an ordinary room name and nick — see the comment
#   above _TS_PROBE.
#
# See FR-rrc-client.md for the source citations. Nothing here touches the
# radio, so it is all host-testable.

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

DEFAULT_MAX_BODY = 350
DEFAULT_MAX_NICK = 32

# Envelope overhead is MEASURED, never assumed -- see envelope_overhead().
# The constant this replaces (ENVELOPE_OVERHEAD = 64, taken from the spec)
# was arithmetic on a guess and it was wrong: the fixed cost alone is 44
# bytes, leaving only ~20 of that 64 for the room name and the nick
# together. A 16-character room plus a 16-character nick and the hub's
# 350-byte body encoded to 433 bytes against a 431-byte link MDU, and urns'
# OutgoingLink.send() has no MDU guard (unlike request()) to catch it: the
# oversized packet goes on air and is dropped or mangled after paying ~1.1 s
# of LoRa airtime.
#
# A worst-case timestamp for the probe: now_ms() reads ~1.76e12, which CBOR
# encodes as a 9-byte uint64, and no realistic mesh clock is smaller. An
# unsynced device sends K_TS 0 (1 byte) instead, so probing with this is
# conservative by 8 bytes rather than optimistic by any.
_TS_PROBE = 1 << 60

# CBOR head sizes by payload length (RFC 8949 major-type argument).
_HEAD_BREAKS = ((24, 1), (256, 2), (65536, 3))

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


def _head_len(n):
    """Bytes the CBOR head of an n-byte string costs."""
    for limit, size in _HEAD_BREAKS:
        if n < limit:
            return size
    return 5


def envelope_overhead(t=T_MSG, src=None, room=None, nick=None):
    """Encoded bytes this session's envelope costs, body excluded.

    Measured with a probe encode rather than counted by hand: room names
    and nicks are variable-length and multi-byte, the timestamp's width
    depends on the clock, and the sum of those guesses is what put a
    433-byte packet on a 431-byte link."""
    probe = make_envelope(t, src=src if src else b"\x00" * 16, room=room,
                          body="", nick=nick, ts=_TS_PROBE)
    return len(cbor.dumps(probe)) - 1       # less the empty body's own head


def body_cap(mdu, hub_limit=None, src=None, room=None, nick=None, t=T_MSG):
    """Longest message body that fits both the hub's rule and the link.

    Both bounds are per session: the hub's WELCOME limit, and whatever is
    left of the link MDU once THIS session's envelope (its room name, its
    nick, its timestamp) has been encoded and measured.

    The hub limit arrives as untrusted CBOR and can be a string, a float,
    a list or negative -- min("350", 367) raises TypeError, and it raises
    on the say() path, which has no try/except between it and kbd_loop.
    An unusable limit falls back to the protocol default, exactly as
    normalize_nick() does with its own budget."""
    if not isinstance(hub_limit, int) or isinstance(hub_limit, bool) \
            or hub_limit <= 0:
        hub_limit = DEFAULT_MAX_BODY
    room_left = mdu - envelope_overhead(t, src, room, nick)
    # Largest n whose head plus payload still fits. At most four steps: the
    # head is 1, 2, 3 or 5 bytes and n only ever walks down within one band.
    n = room_left - 1
    while n > 0 and n + _head_len(n) > room_left:
        n -= 1
    # 0 is a real answer, not a floor to be papered over: a link whose MDU
    # the room name and nick already fill has no room for a body at all,
    # and encode_capped() refuses that packet rather than truncating it to
    # one character and sending it anyway.
    return max(0, min(hub_limit, n))


def encode_capped(env, mdu):
    """Encode env for the link, trimming K_BODY until the bytes fit.

    body_cap() sizes the composer, but only the encoder knows what a given
    envelope really costs, so the packet that goes on air is measured here
    too -- and env is trimmed in step, so the caller can echo exactly what
    was sent. Returns None only if even a bodiless envelope will not fit,
    which means the link is unusable rather than the message too long."""
    data = cbor.dumps(env)
    if len(data) <= mdu:
        return data
    body = env.get(K_BODY)
    if isinstance(body, str):
        # One character is at least one byte, so dropping `over` characters
        # drops at least `over` bytes: this converges in a step or two.
        while body and len(data) > mdu:
            over = len(data) - mdu
            body = body[:-over] if over < len(body) else ""
            env[K_BODY] = body
            data = cbor.dumps(env)
        if len(data) <= mdu:
            return data
    if K_BODY in env:
        # Nothing trimmable left -- a bytes/int body, or an envelope whose
        # room and nick alone fill the link. rrcd ignores the PONG body it
        # asked us to echo (router.py:161-164), so dropping the body answers
        # the ping rather than putting an oversized packet on air or going
        # silent and being closed for it.
        del env[K_BODY]
        data = cbor.dumps(env)
        if len(data) <= mdu:
            return data
    return None


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
