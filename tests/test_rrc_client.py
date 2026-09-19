# Host tests for rrc_client discovery and inbound dispatch. Bootstraps
# urns via the submodule harness. Run:
#   /opt/homebrew/bin/python3 tests/test_rrc_client.py

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
FW_TESTS = os.path.join(REPO, "vendor", "uP-reticulum", "firmware", "tests")

sys.path.insert(0, FW_TESTS)
import harness  # noqa: F401
sys.path.insert(0, REPO)

sys.modules.setdefault("uasyncio", types.ModuleType("uasyncio"))

import rrc_cbor as C
import rrc_proto as P
import rrc_client


class FakeGui:
    def __init__(self):
        self.hubs = []
        self.cleared = 0
        self.status = []
        self._wake_mode = 0

    def add_rrc_hub(self, dest_hash, name=None, hops=None):
        self.hubs.append((dest_hash, name, hops))

    def clear_rrc_hubs(self):
        self.cleared += 1

    def rrc_status(self, text):
        self.status.append(text)

    def wake_screen(self):
        pass


def _reset():
    g = FakeGui()
    rrc_client.init(g, types.SimpleNamespace(hash=b"\x69" * 16))
    rrc_client._hub_hash = lambda h: h      # every hash is an rrc.hub hash
    rrc_client._node_hops = lambda h: 2
    return g


def test_announce_with_hub_name_is_listed():
    g = _reset()
    app_data = C.dumps({"proto": "rrc", "v": 1, "hub": "Varna Hub"})
    rrc_client._on_announce(b"\x42" * 16, app_data, None)
    assert g.hubs == [(b"\x42" * 16, "Varna Hub", 2)], g.hubs
    print("ok test_announce_with_hub_name_is_listed")


def test_announce_without_app_data_still_lists_the_hub():
    g = _reset()
    rrc_client._on_announce(b"\x43" * 16, None, None)
    assert g.hubs == [(b"\x43" * 16, None, 2)], g.hubs
    print("ok test_announce_without_app_data_still_lists_the_hub")


def test_announce_with_junk_app_data_does_not_raise():
    g = _reset()
    rrc_client._on_announce(b"\x44" * 16, b"\xff\xff\xff", None)
    assert g.hubs == [(b"\x44" * 16, None, 2)], g.hubs
    print("ok test_announce_with_junk_app_data_does_not_raise")


def test_non_rrc_announce_is_ignored():
    g = _reset()
    rrc_client._hub_hash = lambda h: b"\x00" * 16    # never matches
    rrc_client._on_announce(b"\x45" * 16, None, None)
    assert g.hubs == []
    print("ok test_non_rrc_announce_is_ignored")


if __name__ == "__main__":
    for name in list(globals()):
        if name.startswith("test_"):
            globals()[name]()
    print("all rrc_client tests passed")
