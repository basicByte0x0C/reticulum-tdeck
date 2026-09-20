# Host tests for the RRC envelope layer. Run:
#   /opt/homebrew/bin/python3 tests/test_rrc_proto.py

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import rrc_cbor as C
import rrc_proto as P


def test_envelope_carries_required_keys():
    env = P.make_envelope(P.T_MSG, src=b"\xab" * 16, room="#varna", body="hi",
                          nick="tdeck")
    for k in (P.K_V, P.K_T, P.K_ID, P.K_TS, P.K_SRC):
        assert k in env, k
    assert env[P.K_V] == 1
    assert env[P.K_T] == P.T_MSG
    assert len(env[P.K_ID]) == 8
    assert env[P.K_ROOM] == "#varna"
    assert env[P.K_BODY] == "hi"
    assert env[P.K_NICK] == "tdeck"
    print("ok test_envelope_carries_required_keys")


def test_envelope_omits_empty_optionals():
    env = P.make_envelope(P.T_PING, src=b"\x01" * 16)
    assert P.K_ROOM not in env and P.K_BODY not in env and P.K_NICK not in env
    print("ok test_envelope_omits_empty_optionals")


def test_parse_rejects_wrong_version_and_junk():
    good = C.dumps(P.make_envelope(P.T_MSG, src=b"\x01" * 16, room="#a", body="x"))
    assert P.parse(good) is not None
    bad_ver = C.dumps({P.K_V: 2, P.K_T: 20, P.K_ID: b"1" * 8, P.K_TS: 0,
                       P.K_SRC: b"\x01" * 16})
    assert P.parse(bad_ver) is None
    assert P.parse(b"\xff\xff\xff") is None
    assert P.parse(C.dumps("not a map")) is None
    print("ok test_parse_rejects_wrong_version_and_junk")


def test_parse_keeps_extension_keys():
    raw = C.dumps({P.K_V: 1, P.K_T: 20, P.K_ID: b"1" * 8, P.K_TS: 0,
                   P.K_SRC: b"\x01" * 16, 64: "reaction"})
    env = P.parse(raw)
    assert env is not None and env[64] == "reaction"
    print("ok test_parse_keeps_extension_keys")


SRC = b"\x69" * 16


def _fits(mdu, hub_limit, room, nick):
    """Encode a max-length body for this session and return its packet size."""
    cap = P.body_cap(mdu, hub_limit, src=SRC, room=room, nick=nick)
    env = P.make_envelope(P.T_MSG, src=SRC, room=room, body="x" * cap,
                          nick=nick, ts=P._TS_PROBE)
    return cap, len(C.dumps(env))


def test_body_cap_is_the_smaller_of_hub_and_link():
    # The link bound is measured, not assumed: with no room and no nick a
    # probe envelope costs 44 bytes, leaving 384 for a body at mdu 431, so
    # the hub's 350 still wins. (The old ENVELOPE_OVERHEAD = 64 put this at
    # 367 -- close enough to look right, and wrong in the direction that
    # overruns the MDU once a room name and a nick are added.)
    assert P.body_cap(431, 350, src=SRC) == 350
    # A lower-MTU path inverts it: the link becomes the binding limit, and
    # the cap is MAXIMAL there -- a body of exactly cap bytes fills the MDU
    # to the byte. A merely-safe cap (the old constant's 136) would leave
    # airtime on the table every message; this pins both directions without
    # re-encoding the arithmetic as a magic number.
    cap = P.body_cap(200, 350, src=SRC)
    assert cap < 350, cap
    env = P.make_envelope(P.T_MSG, src=SRC, body="x" * cap, ts=P._TS_PROBE)
    assert len(C.dumps(env)) == 200, len(C.dumps(env))
    # No WELCOME limits yet: fall back to the protocol default.
    assert P.body_cap(431, None, src=SRC) == 350
    print("ok test_body_cap_is_the_smaller_of_hub_and_link")


def test_the_encoded_packet_never_exceeds_the_link_mdu():
    # THE invariant. urns' OutgoingLink.send() has no MDU guard (unlike
    # request()), so an oversized packet goes on air and is dropped or
    # mangled -- ~1.1 s of LoRa airtime for nothing. Measured against the
    # real encoder for the worst realistic session: a long room name, a
    # full-length nick and the hub's maximum body.
    cyr_room = "#варна"
    cyr_nick = "милен"
    for room, nick in (("#varna-mesh-chat", "milen-tdeck-node"),
                       ("#" + "r" * 60, "n" * 32),
                       (None, None),
                       ("#a", None),
                       (cyr_room, cyr_nick)):          # multi-byte room + nick
        for mdu in (431, 300, 200, 120, 64):
            cap, size = _fits(mdu, 350, room, nick)
            if cap == 0:
                # The room name and nick alone fill this link. That must be
                # refused outright, not truncated to one character and sent.
                env = P.make_envelope(P.T_MSG, src=SRC, room=room, body="x",
                                      nick=nick, ts=P._TS_PROBE)
                assert P.encode_capped(env, mdu) is None, (room, nick, mdu)
                continue
            assert size <= mdu, (room, nick, mdu, cap, size)
    # ...and the link we actually run on always has room for a real message.
    for room, nick in (("#varna-mesh-chat", "milen-tdeck-node"),
                       ("#" + "r" * 60, "n" * 32)):
        assert P.body_cap(431, 350, src=SRC, room=room, nick=nick) > 200
    print("ok test_the_encoded_packet_never_exceeds_the_link_mdu")


def test_body_cap_shrinks_as_the_room_and_nick_grow():
    # The old constant could not: 64 bytes regardless of what the envelope
    # actually carried.
    bare = P.body_cap(431, 400, src=SRC)
    roomed = P.body_cap(431, 400, src=SRC, room="#" + "r" * 40)
    nicked = P.body_cap(431, 400, src=SRC, room="#" + "r" * 40, nick="n" * 32)
    assert bare > roomed > nicked, (bare, roomed, nicked)
    assert bare - roomed >= 40, (bare, roomed)
    assert roomed - nicked >= 32, (roomed, nicked)
    print("ok test_body_cap_shrinks_as_the_room_and_nick_grow")


def test_body_cap_ignores_a_hub_limit_that_is_not_a_positive_int():
    # A hub sending {L_MAX_BODY: "350"} used to reach min("350", 367) and
    # raise TypeError straight through rrc_ui.handle_key into kbd_loop,
    # taking the keyboard and the trackball with it. normalize_nick already
    # validates its budget this way; body_cap is the one that did not.
    default = P.body_cap(431, None, src=SRC)
    for bad in ("350", None, 0, -1, 3.5, [350], {}):
        cap = P.body_cap(431, bad, src=SRC)
        assert isinstance(cap, int) and cap > 0, (bad, cap)
        assert cap == default, (bad, cap, default)
    print("ok test_body_cap_ignores_a_hub_limit_that_is_not_a_positive_int")


def test_encode_capped_trims_a_body_that_would_overrun_the_link():
    # The belt-and-braces half: whatever the cap arithmetic said, the bytes
    # handed to link.send() are measured and trimmed until they fit.
    env = P.make_envelope(P.T_MSG, src=SRC, room="#varna", body="x" * 5000,
                          nick="tdeck")
    data = P.encode_capped(env, 431)
    assert data is not None and len(data) <= 431, len(data or b"")
    # The caller echoes what was actually sent, so the trim has to be
    # visible in the envelope it handed over, not only in the bytes.
    assert P.parse(data)[P.K_BODY] == env[P.K_BODY], "env was not kept in step"
    print("ok test_encode_capped_trims_a_body_that_would_overrun_the_link")


def test_encode_capped_drops_an_untrimmable_body_rather_than_overrunning():
    # A hub PING carrying ~400 bytes: the PONG echoes the body, and a bytes
    # body cannot be trimmed as text. rrcd ignores the PONG body entirely,
    # so dropping it answers the ping instead of putting an oversized packet
    # on air -- or staying silent and being closed for it.
    env = P.make_envelope(P.T_PONG, src=SRC, body=b"\xab" * 420)
    data = P.encode_capped(env, 431)
    assert data is not None and len(data) <= 431, len(data or b"")
    assert P.K_BODY not in P.parse(data)
    print("ok test_encode_capped_drops_an_untrimmable_body_rather_than_overrunning")


def test_now_ms_is_zero_when_clock_unsynced():
    assert P.now_ms(epoch_s=0) == 0
    assert P.now_ms(epoch_s=1758000000) == 1758000000000
    print("ok test_now_ms_is_zero_when_clock_unsynced")


def test_normalize_nick_truncates_on_bytes_not_chars():
    assert P.normalize_nick("  tdeck  ") == "tdeck"
    assert P.normalize_nick("") is None
    assert P.normalize_nick("a\nb") == "ab"
    long_utf8 = "ä" * 40          # 2 bytes each
    out = P.normalize_nick(long_utf8, max_bytes=32)
    assert len(out.encode("utf-8")) <= 32
    print("ok test_normalize_nick_truncates_on_bytes_not_chars")


def test_now_ms_handles_micropython_2000_epoch():
    real_time, real_localtime = P.time.time, P.time.localtime
    try:
        P.time.localtime = lambda *a: (2026, 9, 19, 12, 0, 0, 0, 0)
        P.time.time = lambda: 843000000.0          # 2000-based device clock
        assert P.now_ms() == int((843000000.0 + P._EPOCH_OFFSET) * 1000)
        P.time.localtime = lambda *a: (2000, 1, 1, 0, 0, 0, 0, 0)
        assert P.now_ms() == 0                     # RTC never set from the mesh
    finally:
        P.time.time, P.time.localtime = real_time, real_localtime
    print("ok test_now_ms_handles_micropython_2000_epoch")


def test_normalize_nick_rejects_impossible_budgets():
    # A hub's WELCOME limits map is untrusted CBOR: any of these would
    # otherwise spin the truncation loop forever.
    assert P.normalize_nick("tdeck", max_bytes=0) is None
    assert P.normalize_nick("tdeck", max_bytes=-1) is None
    assert P.normalize_nick("tdeck", max_bytes="32") is None
    assert P.normalize_nick("tdeck", max_bytes=None) is None
    print("ok test_normalize_nick_rejects_impossible_budgets")


def test_normalize_nick_budget_smaller_than_one_character():
    # One 2-byte character against a 1-byte budget: must terminate and
    # must never emit half a UTF-8 sequence.
    out = P.normalize_nick("ä", max_bytes=1)
    assert out is None, out
    print("ok test_normalize_nick_budget_smaller_than_one_character")


if __name__ == "__main__":
    for name in list(globals()):
        if name.startswith("test_"):
            globals()[name]()
    print("all rrc_proto tests passed")
