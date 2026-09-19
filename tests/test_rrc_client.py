# Host tests for rrc_client discovery and inbound dispatch. Bootstraps
# urns via the submodule harness. Run:
#   /opt/homebrew/bin/python3 tests/test_rrc_client.py

import os
import sys
import time
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
        self.members = []           # rrc_members() snapshots, newest last

    def add_rrc_hub(self, dest_hash, name=None, hops=None):
        self.hubs.append((dest_hash, name, hops))

    def clear_rrc_hubs(self):
        self.cleared += 1

    def rrc_status(self, text):
        self.status.append(text)

    def wake_screen(self):
        pass

    def rrc_closed(self):
        pass

    def rrc_members(self, members):
        # _roster_changed() calls this alongside rrc_roster() on every
        # roster change (Task 9) -- every FakeGui needs it or the fixture
        # crashes, not just the ones with a _session()-installed override.
        self.members.append(members)


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


class FakeLink:
    def __init__(self):
        self.sent = []
        self.mdu = 431
        self.torn_down = False

    def send(self, data):
        self.sent.append(bytes(data))

    def teardown(self):
        # urns' OutgoingLink.teardown() invokes closed_callback synchronously
        # (link.py _close). The fake must too, or the tests cannot see a
        # teardown that paints a spurious error at the user.
        self.torn_down = True
        rrc_client._on_link_closed(self)


def _session(room="#varna"):
    g = _reset()
    g.lines = []
    g.rosters = []
    g.joined = []
    g.welcomed = []
    g.rrc_line = lambda kind, nick, text: g.lines.append((kind, nick, text))
    g.rrc_roster = lambda count: g.rosters.append(count)
    g.rrc_joined = lambda r: g.joined.append(r)
    g.rrc_welcome = lambda name: g.welcomed.append(name)
    link = FakeLink()
    rrc_client._link = link
    rrc_client._state = rrc_client.JOINED
    rrc_client._room = room
    rrc_client._roster = {}
    rrc_client._seen_ids = []
    return g, link


def _env(t, **kw):
    kw.setdefault("src", b"\xaa" * 16)
    return C.dumps(P.make_envelope(t, **kw))


def test_ping_is_answered_with_pong_echoing_the_body():
    g, link = _session()
    rrc_client._on_packet(_env(P.T_PING, body=12345))
    assert len(link.sent) == 1
    reply = P.parse(link.sent[0])
    assert reply[P.K_T] == P.T_PONG
    assert reply[P.K_BODY] == 12345
    print("ok test_ping_is_answered_with_pong_echoing_the_body")


def test_msg_renders_with_its_nick():
    g, link = _session()
    rrc_client._on_packet(_env(P.T_MSG, room="#varna", body="hi there",
                               nick="kc1awv"))
    assert g.lines == [("msg", "kc1awv", "hi there")], g.lines
    print("ok test_msg_renders_with_its_nick")


def test_action_is_rendered_not_dropped():
    g, link = _session()
    rrc_client._on_packet(_env(P.T_ACTION, room="#varna", body="waves",
                               nick="sam"))
    assert g.lines == [("action", "sam", "waves")], g.lines
    print("ok test_action_is_rendered_not_dropped")


def test_duplicate_msg_id_is_rendered_once():
    g, link = _session()
    mid = b"\x07" * 8
    for _ in range(2):
        rrc_client._on_packet(_env(P.T_MSG, room="#varna", body="replayed",
                                   nick="sam", mid=mid))
    assert len(g.lines) == 1, g.lines
    print("ok test_duplicate_msg_id_is_rendered_once")


def test_joined_body_seeds_the_roster():
    g, link = _session()
    rrc_client._state = rrc_client.READY          # we sent JOIN, not in yet
    rrc_client._roster = {b"\x99" * 16: "stale"}  # left from a previous room
    members = [b"\x11" * 16, b"\x22" * 16, b"\x33" * 16]
    rrc_client._on_packet(C.dumps(P.make_envelope(
        P.T_JOINED, src=b"\xaa" * 16, room="#varna", body=members)))
    assert rrc_client._state == rrc_client.JOINED
    assert g.joined == ["#varna"], g.joined
    assert set(rrc_client._roster) == set(members), rrc_client._roster
    assert b"\x99" * 16 not in rrc_client._roster, "stale member survived the reseed"
    assert g.rosters[-1] == 3
    print("ok test_joined_body_seeds_the_roster")


def test_roster_changed_pushes_both_the_count_and_the_member_snapshot():
    # Task 9 rewrote _roster_changed() to call rrc_members() alongside the
    # existing rrc_roster() -- both must fire on the same change, or either
    # the room header (count) or the member panel (snapshot) goes stale.
    # This also pins the sort: named members first (case-insensitively),
    # unnamed (never-spoken, nick is None) last -- the shape the panel
    # renders as "nick or ?" in list order.
    g, link = _session()
    rrc_client._roster = {
        b"\x33" * 16: None,
        b"\x11" * 16: "Sam",
        b"\x22" * 16: "kc1awv",
    }
    rrc_client._roster_changed()
    assert g.rosters[-1] == 3, g.rosters
    snap = g.members[-1]
    assert snap == [
        (b"\x22" * 16, "kc1awv"),
        (b"\x11" * 16, "Sam"),
        (b"\x33" * 16, None),
    ], snap
    print("ok test_roster_changed_pushes_both_the_count_and_the_member_snapshot")


def test_joined_event_adds_a_member_and_parted_removes_one():
    g, link = _session()
    who = b"\x55" * 16
    rrc_client._on_packet(C.dumps(P.make_envelope(
        P.T_JOINED, src=b"\xaa" * 16, room="#varna", body=[who], nick="sam")))
    assert who in rrc_client._roster
    assert len(rrc_client._roster) == 1
    rrc_client._on_packet(C.dumps(P.make_envelope(
        P.T_PARTED, src=b"\xaa" * 16, room="#varna", body=[who], nick="sam")))
    assert who not in rrc_client._roster
    assert len(rrc_client._roster) == 0
    print("ok test_joined_event_adds_a_member_and_parted_removes_one")


def test_joined_event_never_adds_the_hub_to_the_roster():
    g, link = _session()                          # already JOINED
    hub = b"\xaa" * 16
    who = b"\x55" * 16
    rrc_client._on_packet(C.dumps(P.make_envelope(
        P.T_JOINED, src=hub, room="#varna", body=[who], nick="sam")))
    assert who in rrc_client._roster
    assert hub not in rrc_client._roster, "K_SRC on JOINED is the hub, not a member"
    assert len(rrc_client._roster) == 1, rrc_client._roster
    assert rrc_client._roster[who] == "sam"
    print("ok test_joined_event_never_adds_the_hub_to_the_roster")


def test_nick_is_learned_from_incoming_messages():
    g, link = _session()
    src = b"\x99" * 16
    rrc_client._on_packet(C.dumps(P.make_envelope(
        P.T_MSG, src=src, room="#varna", body="hi", nick="hilltop-rx")))
    assert rrc_client._roster.get(src) == "hilltop-rx"
    print("ok test_nick_is_learned_from_incoming_messages")


def test_notice_renders_as_plain_text():
    g, link = _session()
    rrc_client._on_packet(_env(P.T_NOTICE, body="members in #varna: sam"))
    assert g.lines == [("notice", None, "members in #varna: sam")], g.lines
    print("ok test_notice_renders_as_plain_text")


def test_banned_error_closes_and_blocks_retry():
    g, link = _session()
    rrc_client._on_packet(_env(P.T_ERROR, body="banned"))
    assert rrc_client._no_retry is True
    assert ("error", None, "banned") in g.lines
    print("ok test_banned_error_closes_and_blocks_retry")


def test_rate_limited_error_sets_backoff():
    g, link = _session()
    rrc_client._on_packet(_env(P.T_ERROR, body="rate limited"))
    assert rrc_client._backoff_until > 0
    assert rrc_client._no_retry is False
    print("ok test_rate_limited_error_sets_backoff")


def test_welcome_records_hub_limits():
    g, link = _session()
    body = {P.B_WELCOME_HUB: "Varna Hub", P.B_WELCOME_VER: "rrcd/0.4",
            P.B_WELCOME_LIMITS: {P.L_MAX_BODY: 350, P.L_MAX_NICK: 32}}
    rrc_client._state = rrc_client.CONNECTING
    rrc_client._on_packet(_env(P.T_WELCOME, body=body))
    assert rrc_client._limits.get(P.L_MAX_BODY) == 350
    assert rrc_client._state == rrc_client.READY
    assert g.welcomed == ["Varna Hub"]
    print("ok test_welcome_records_hub_limits")


def test_welcome_without_caps_map_is_accepted():
    g, link = _session()
    rrc_client._state = rrc_client.CONNECTING
    rrc_client._on_packet(_env(P.T_WELCOME, body={P.B_WELCOME_HUB: "Go Hub"}))
    assert rrc_client._state == rrc_client.READY
    print("ok test_welcome_without_caps_map_is_accepted")


def test_resource_envelope_is_ignored_quietly():
    g, link = _session()
    before = len(g.lines)
    rrc_client._on_packet(_env(P.T_RESOURCE_ENVELOPE, body={0: b"12345678"}))
    assert len(g.lines) == before
    print("ok test_resource_envelope_is_ignored_quietly")


def test_unknown_type_and_junk_do_not_raise():
    g, link = _session()
    rrc_client._on_packet(_env(99, body="whatever"))
    rrc_client._on_packet(b"\xff\xff")
    print("ok test_unknown_type_and_junk_do_not_raise")


def test_say_sends_a_msg_envelope_with_room_and_nick():
    g, link = _session()
    g.node_name = "tdeck"
    rrc_client.say("gm from varna")
    env = P.parse(link.sent[-1])
    assert env[P.K_T] == P.T_MSG
    assert env[P.K_ROOM] == "#varna"
    assert env[P.K_BODY] == "gm from varna"
    assert env[P.K_NICK] == "tdeck"
    print("ok test_say_sends_a_msg_envelope_with_room_and_nick")


def test_say_sends_action_for_slash_me():
    g, link = _session()
    g.node_name = "tdeck"
    rrc_client.say("/me waves")
    env = P.parse(link.sent[-1])
    assert env[P.K_T] == P.T_ACTION
    assert env[P.K_BODY] == "waves"
    print("ok test_say_sends_action_for_slash_me")


def test_say_passes_other_slash_commands_through_as_msg():
    g, link = _session()
    g.node_name = "tdeck"
    rrc_client.say("/who")
    env = P.parse(link.sent[-1])
    assert env[P.K_T] == P.T_MSG and env[P.K_BODY] == "/who"
    print("ok test_say_passes_other_slash_commands_through_as_msg")


def test_say_truncates_to_the_computed_cap():
    g, link = _session()
    g.node_name = "tdeck"
    rrc_client._limits = {P.L_MAX_BODY: 20}
    rrc_client.say("x" * 100)
    env = P.parse(link.sent[-1])
    assert len(env[P.K_BODY]) == 20
    print("ok test_say_truncates_to_the_computed_cap")


def test_say_truncates_on_utf8_bytes_not_characters():
    g, link = _session()
    g.node_name = "tdeck"
    rrc_client._limits = {P.L_MAX_BODY: 10}
    rrc_client.say("ä" * 20)          # 2 bytes each, 40 bytes total
    env = P.parse(link.sent[-1])
    assert len(env[P.K_BODY].encode("utf-8")) <= 10
    print("ok test_say_truncates_on_utf8_bytes_not_characters")


def test_say_is_refused_while_rate_limited():
    g, link = _session()
    g.node_name = "tdeck"
    rrc_client._backoff_until = time.time() + 10
    sent_before = len(link.sent)
    rrc_client.say("hello")
    assert len(link.sent) == sent_before
    rrc_client._backoff_until = 0
    print("ok test_say_is_refused_while_rate_limited")


def test_join_sends_room_and_optional_key():
    g, link = _session(room=None)
    rrc_client._state = rrc_client.READY
    rrc_client.join("#varna")
    env = P.parse(link.sent[-1])
    assert env[P.K_T] == P.T_JOIN and env[P.K_ROOM] == "#varna"
    assert P.K_BODY not in env
    rrc_client.join("#secret", key="hunter2")
    env = P.parse(link.sent[-1])
    assert env[P.K_BODY] == "hunter2"
    print("ok test_join_sends_room_and_optional_key")


def test_join_lowercases_the_room_name():
    g, link = _session(room=None)
    rrc_client._state = rrc_client.READY
    rrc_client.join("  #Varna  ")
    env = P.parse(link.sent[-1])
    assert env[P.K_ROOM] == "#varna"
    print("ok test_join_lowercases_the_room_name")


def test_part_sends_part_and_returns_to_ready():
    g, link = _session()
    rrc_client.part()
    env = P.parse(link.sent[-1])
    assert env[P.K_T] == P.T_PART and env[P.K_ROOM] == "#varna"
    assert rrc_client._state == rrc_client.READY
    assert rrc_client._room is None
    print("ok test_part_sends_part_and_returns_to_ready")


def test_mention_uses_at_nick_when_unique():
    g, link = _session()
    rrc_client._roster = {b"\x11" * 16: "sam", b"\x22" * 16: "kc1awv"}
    assert rrc_client.mention_for(b"\x11" * 16) == "@sam"
    print("ok test_mention_uses_at_nick_when_unique")


def test_mention_falls_back_to_hash_when_ambiguous_or_unknown():
    g, link = _session()
    dup = b"\x11" * 16
    rrc_client._roster = {dup: "sam", b"\x22" * 16: "sam"}
    assert rrc_client.mention_for(dup) == "@" + dup.hex()[:8]
    silent = b"\x33" * 16
    rrc_client._roster[silent] = None
    assert rrc_client.mention_for(silent) == "@" + silent.hex()[:8]
    print("ok test_mention_falls_back_to_hash_when_ambiguous_or_unknown")


def test_intentional_disconnect_is_not_reported_as_a_dropped_link():
    g, link = _session()
    rrc_client.disconnect()
    assert link.torn_down is True
    assert not [ln for ln in g.lines if ln[0] == "error"], g.lines
    assert rrc_client._link is None
    print("ok test_intentional_disconnect_is_not_reported_as_a_dropped_link")


def test_join_while_joined_parts_the_old_room_first():
    g, link = _session(room="#varna")          # already JOINED
    rrc_client.join("#dx")
    kinds = [P.parse(p)[P.K_T] for p in link.sent[-2:]]
    assert kinds == [P.T_PART, P.T_JOIN], kinds
    assert P.parse(link.sent[-2])[P.K_ROOM] == "#varna"
    assert P.parse(link.sent[-1])[P.K_ROOM] == "#dx"
    assert rrc_client._state == rrc_client.READY   # awaiting the JOIN reply
    print("ok test_join_while_joined_parts_the_old_room_first")


if __name__ == "__main__":
    for name in list(globals()):
        if name.startswith("test_"):
            globals()[name]()
    print("all rrc_client tests passed")
