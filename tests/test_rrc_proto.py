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


def test_body_cap_is_the_smaller_of_hub_and_link():
    # MTU 500 -> mdu 431; 431 - 64 = 367, so the hub's 350 wins.
    assert P.body_cap(431, 350) == 350
    # A lower-MTU path inverts it.
    assert P.body_cap(200, 350) == 136
    # No WELCOME limits yet: fall back to the protocol default.
    assert P.body_cap(431, None) == 350
    print("ok test_body_cap_is_the_smaller_of_hub_and_link")


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
