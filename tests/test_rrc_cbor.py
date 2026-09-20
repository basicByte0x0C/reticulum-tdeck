# Host tests for the subset CBOR codec. Run:
#   /opt/homebrew/bin/python3 tests/test_rrc_cbor.py

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import rrc_cbor as C


def test_encodes_envelope_shape():
    # {0:1, 1:20, 5:"#varna", 6:"hi"} - hand-computed against RFC 8949.
    env = {0: 1, 1: 20, 5: "#varna", 6: "hi"}
    assert C.dumps(env) == bytes.fromhex("a4000101140566237661726e6106626869")
    print("ok test_encodes_envelope_shape")


def test_uint_widths():
    assert C.dumps(23) == bytes.fromhex("17")
    assert C.dumps(24) == bytes.fromhex("1818")
    assert C.dumps(256) == bytes.fromhex("190100")
    assert C.dumps(65536) == bytes.fromhex("1a00010000")
    assert C.dumps(4294967296) == bytes.fromhex("1b0000000100000000")
    print("ok test_uint_widths")


def test_round_trip_real_envelope():
    env = {0: 1, 1: 20, 2: b"\x01" * 8, 3: 1758000000000,
           4: b"\xab" * 16, 5: "#varna", 6: "gm from varna", 7: "tdeck"}
    assert C.loads(C.dumps(env)) == env
    print("ok test_round_trip_real_envelope")


def test_decodes_joined_member_array():
    # [b"<16>", b"<16>"] as JOINED bodies arrive.
    raw = bytes.fromhex("82") + b"\x50" + b"\x11" * 16 + b"\x50" + b"\x22" * 16
    assert C.loads(raw) == [b"\x11" * 16, b"\x22" * 16]
    print("ok test_decodes_joined_member_array")


def test_skips_float_extension_key_without_raising():
    # Extension key 64 carrying a float64 must decode, not explode.
    raw = bytes.fromhex("a11840fb3ff0000000000000")
    assert C.loads(raw) == {64: None}
    print("ok test_skips_float_extension_key_without_raising")


def test_unwraps_tag():
    assert C.loads(bytes.fromhex("c0626869")) == "hi"
    print("ok test_unwraps_tag")


def test_bool_and_null():
    assert C.loads(C.dumps(True)) is True
    assert C.loads(C.dumps(False)) is False
    assert C.loads(C.dumps(None)) is None
    print("ok test_bool_and_null")


if __name__ == "__main__":
    for name in list(globals()):
        if name.startswith("test_"):
            globals()[name]()
    print("all rrc_cbor tests passed")
