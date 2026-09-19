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


# --- Important 7: a hub-side /join must not desync the client --------------


def test_hub_side_join_reply_is_read_as_our_own_join_not_an_arrival():
    # Slash commands reach the hub verbatim (that is what makes its command
    # set free), so "/join #other" typed in the composer joins us without
    # join() ever running: the JOINED reply lands while _state is already
    # JOINED. Read as somebody else arriving, _room kept pointing at the old
    # room -- every later send carried the wrong K_ROOM, the header lied and
    # the roster was polluted with the new room's members.
    g, link = _session(room="#varna")                # already JOINED
    rrc_client._roster = {b"\x77" * 16: "oldtimer"}
    members = [b"\x11" * 16, b"\x22" * 16]
    rrc_client._on_packet(C.dumps(P.make_envelope(
        P.T_JOINED, src=b"\xaa" * 16, room="#other", body=members)))
    assert rrc_client._room == "#other", rrc_client._room
    assert g.joined == ["#other"], g.joined
    assert set(rrc_client._roster) == set(members), rrc_client._roster
    assert b"\x77" * 16 not in rrc_client._roster, "old room's roster survived"
    # And the next message must carry the room we are actually in.
    g.node_name = "tdeck"
    rrc_client.say("gm")
    assert P.parse(link.sent[-1])[P.K_ROOM] == "#other"
    print("ok test_hub_side_join_reply_is_read_as_our_own_join_not_an_arrival")


def test_an_arrival_in_the_room_we_are_in_is_still_an_arrival():
    # The other side of the discriminator: a JOINED echoing OUR room while
    # we are JOINED is somebody else arriving, and must add to the roster
    # rather than clearing it and re-firing rrc_joined().
    g, link = _session(room="#varna")
    rrc_client._roster = {b"\x77" * 16: "oldtimer"}
    who = b"\x55" * 16
    rrc_client._on_packet(C.dumps(P.make_envelope(
        P.T_JOINED, src=b"\xaa" * 16, room="#varna", body=[who], nick="sam")))
    assert g.joined == [], g.joined
    assert rrc_client._room == "#varna"
    assert b"\x77" * 16 in rrc_client._roster, "an arrival wiped the roster"
    assert rrc_client._roster[who] == "sam"
    assert ("event", None, "* sam joined") in g.lines, g.lines
    print("ok test_an_arrival_in_the_room_we_are_in_is_still_an_arrival")


def test_an_arrival_without_a_room_field_is_not_mistaken_for_our_own_join():
    # A hub that omits K_ROOM from its arrival broadcasts would otherwise
    # have every arrival look like a join and wipe the roster -- the room
    # comparison only decides when the hub actually named a room.
    g, link = _session(room="#varna")
    rrc_client._roster = {b"\x77" * 16: "oldtimer"}
    who = b"\x55" * 16
    rrc_client._on_packet(C.dumps(P.make_envelope(
        P.T_JOINED, src=b"\xaa" * 16, body=[who], nick="sam")))   # no K_ROOM
    assert g.joined == [], g.joined
    assert rrc_client._room == "#varna", rrc_client._room
    assert b"\x77" * 16 in rrc_client._roster, "a roomless arrival wiped the roster"
    print("ok test_an_arrival_without_a_room_field_is_not_mistaken_for_our_own_join")


# --- Important 6: a refused send must not vanish silently ------------------


def test_a_rate_limited_send_says_so_in_the_scrollback():
    # rrc_ui clears the composer and ignores say()'s return value, and
    # draw_room() never renders _rrc_status -- so a _status() call alone is
    # invisible. The scrollback IS rendered.
    g, link = _session()
    g.node_name = "tdeck"
    rrc_client._backoff_until = time.time() + 10
    sent_before = len(link.sent)
    assert rrc_client.say("hello") is False
    rrc_client._backoff_until = 0
    assert len(link.sent) == sent_before
    assert ("error", None, "not sent: rate limited") in g.lines, g.lines
    print("ok test_a_rate_limited_send_says_so_in_the_scrollback")


def test_a_send_with_no_room_joined_says_so_in_the_scrollback():
    g, link = _session()
    g.node_name = "tdeck"
    rrc_client._state = rrc_client.READY          # connected, not in a room
    assert rrc_client.say("hello") is False
    assert link.sent == []
    assert ("error", None, "not sent: not in a room") in g.lines, g.lines
    print("ok test_a_send_with_no_room_joined_says_so_in_the_scrollback")


def test_a_failed_send_says_so_after_the_local_echo():
    # The local echo (below) makes a silent send failure actively
    # misleading: the user sees their own message and assumes it went.
    g, link = _session()
    g.node_name = "tdeck"

    def _boom(data):
        raise OSError("link gone")

    link.send = _boom
    assert rrc_client.say("gm") is False
    assert ("msg", "tdeck", "gm") in g.lines, g.lines
    assert ("error", None, "not sent: send failed") in g.lines, g.lines
    print("ok test_a_failed_send_says_so_after_the_local_echo")


# --- Important 9: settle self-echo -----------------------------------------


def test_say_echoes_locally_so_a_room_is_never_silent():
    # Neither the spec nor the plan settles whether rrcd fans a message back
    # to its sender. If it does not, typing into a room appeared to do
    # nothing at all.
    g, link = _session()
    g.node_name = "tdeck"
    rrc_client.say("gm from varna")
    assert g.lines == [("msg", "tdeck", "gm from varna")], g.lines
    print("ok test_say_echoes_locally_so_a_room_is_never_silent")


def test_slash_me_echoes_as_an_action_not_a_message():
    g, link = _session()
    g.node_name = "tdeck"
    rrc_client.say("/me waves")
    assert g.lines == [("action", "tdeck", "waves")], g.lines
    print("ok test_slash_me_echoes_as_an_action_not_a_message")


def test_a_hub_echo_of_our_own_message_renders_only_once():
    # The other half: a hub that DOES fan the message back hands us our own
    # envelope, K_ID and all. Pre-seeding _seen() with that id is what stops
    # it being painted twice.
    g, link = _session()
    g.node_name = "tdeck"
    rrc_client.say("gm from varna")
    assert len(g.lines) == 1, g.lines
    # Replay the exact envelope we put on the wire, as a hub fan-out would.
    rrc_client._on_packet(link.sent[-1])
    assert len(g.lines) == 1, g.lines
    print("ok test_a_hub_echo_of_our_own_message_renders_only_once")


def test_a_different_message_with_a_new_id_still_renders():
    # Guards the dedupe from over-reaching: pre-seeding our own id must not
    # suppress anybody else's message.
    g, link = _session()
    g.node_name = "tdeck"
    rrc_client.say("gm")
    rrc_client._on_packet(_env(P.T_MSG, room="#varna", body="hi", nick="sam"))
    assert g.lines == [("msg", "tdeck", "gm"), ("msg", "sam", "hi")], g.lines
    print("ok test_a_different_message_with_a_new_id_still_renders")


# --- Important 3: disconnect() must cancel an in-flight session task -------
#
# These drive the REAL _session_task coroutine. uasyncio's sleeps are
# replaced with a primitive that suspends on every await, so the test can
# step the task and call disconnect() at a chosen point; urns' identity,
# transport, link and destination modules are swapped for stubs for the
# duration. Nothing here fakes rrc_client itself.


class _Yield:
    def __await__(self):
        yield


async def _fake_sleep(*a):
    await _Yield()


class _FakeOutgoingLink:
    PENDING = 0
    ACTIVE = 1
    CLOSED = 2
    instances = []
    initial = 0            # PENDING unless a test says otherwise

    def __init__(self, dst, closed_callback=None, sign_proofs=False):
        self.status = _FakeOutgoingLink.initial
        self.mdu = 431
        self.sent = []
        self.torn_down = False
        self.identified = None
        self.packet_callback = None
        self.closed_callback = closed_callback
        _FakeOutgoingLink.instances.append(self)

    def send(self, data):
        self.sent.append(bytes(data))

    def set_packet_callback(self, cb):
        self.packet_callback = cb

    def identify(self, identity):
        self.identified = identity

    def teardown(self):
        self.torn_down = True
        self.status = _FakeOutgoingLink.CLOSED
        if self.closed_callback is not None:
            self.closed_callback(self)


class _FakeDestination:
    OUT = 1
    SINGLE = 0

    def __init__(self, *a):
        pass


def _install_session_stubs(path_found_after=0):
    """Swap uasyncio and the four urns modules _session_task imports.

    Returns (restore_callable, transport_stub). path_found_after is the
    number of has_path() calls that answer False before the path appears,
    which is how the "finding path..." phase is held open.
    """
    saved = {}
    for name in ("uasyncio", "urns.identity", "urns.transport",
                 "urns.link", "urns.destination"):
        saved[name] = sys.modules.get(name)

    aio = types.ModuleType("uasyncio")
    aio.sleep = _fake_sleep
    aio.sleep_ms = _fake_sleep
    aio.create_task = lambda coro: coro
    sys.modules["uasyncio"] = aio

    state = {"calls": 0}

    class _Transport:
        requested = []

        @staticmethod
        def has_path(h):
            state["calls"] += 1
            return state["calls"] > path_found_after

        @staticmethod
        def request_path(h):
            _Transport.requested.append(h)

        @staticmethod
        def hops_to(h):
            return 1

    class _Identity:
        @staticmethod
        def recall(h):
            return object() if state["calls"] > path_found_after else None

    m = types.ModuleType("urns.identity")
    m.Identity = _Identity
    sys.modules["urns.identity"] = m
    m = types.ModuleType("urns.transport")
    m.Transport = _Transport
    sys.modules["urns.transport"] = m
    m = types.ModuleType("urns.link")
    m.OutgoingLink = _FakeOutgoingLink
    sys.modules["urns.link"] = m
    m = types.ModuleType("urns.destination")
    m.Destination = _FakeDestination
    sys.modules["urns.destination"] = m

    _FakeOutgoingLink.instances = []
    _FakeOutgoingLink.initial = _FakeOutgoingLink.PENDING
    _Transport.requested = []

    def restore():
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod

    return restore, _Transport


def _idle_session():
    g, link = _session()
    g.welcomed = []
    rrc_client._link = None
    rrc_client._state = rrc_client.IDLE
    rrc_client._no_retry = False
    rrc_client._backoff_until = 0
    return g


def _step(coro, n=1):
    """Advance the coroutine n await points. Returns True once it finished."""
    for _ in range(n):
        try:
            coro.send(None)
        except StopIteration:
            return True
    return False


def test_disconnect_while_finding_a_path_stops_the_session_task_dead():
    # Back out during "finding path...": without cancellation the task went
    # on to establish the link, identify, send HELLO and -- on WELCOME --
    # call _gui.rrc_welcome(), which forces state = STATE_RRC_ROOMS and
    # yanks the user out of an unrelated LXMF conversation. It also left a
    # link nobody would tear down, paying LoRa airtime against the
    # on-demand-session decision.
    g = _idle_session()
    restore, transport = _install_session_stubs(path_found_after=1)
    try:
        # The path turns up on the very next poll, so a task that keeps
        # running gets all the way to HELLO rather than timing out.
        _FakeOutgoingLink.initial = _FakeOutgoingLink.ACTIVE
        coro = rrc_client._session_task(b"\x42" * 16)
        _step(coro)                       # into the path wait
        assert rrc_client._state == rrc_client.CONNECTING
        rrc_client.disconnect()           # the user backs out
        assert _step(coro, 60), "the session task kept running after disconnect"
        assert _FakeOutgoingLink.instances == [], "a link was opened anyway"
        assert g.welcomed == [], g.welcomed
        assert rrc_client._link is None
        assert rrc_client._state == rrc_client.CLOSED
    finally:
        restore()
    print("ok test_disconnect_while_finding_a_path_stops_the_session_task_dead")


def test_disconnect_while_linking_tears_down_the_pending_candidate():
    # One step further in: the link request is out, so the task owns a
    # candidate nobody else can see. Abandoning it without a teardown leaves
    # a half-open link on the hub.
    g = _idle_session()
    restore, transport = _install_session_stubs()
    try:
        coro = rrc_client._session_task(b"\x42" * 16)
        _step(coro, 2)                    # past sleep_ms(50), into the wait
        assert len(_FakeOutgoingLink.instances) == 1, _FakeOutgoingLink.instances
        candidate = _FakeOutgoingLink.instances[0]
        assert candidate.torn_down is False
        rrc_client.disconnect()
        assert _step(coro, 60), "the session task kept running after disconnect"
        assert candidate.torn_down is True, "the candidate link was orphaned"
        assert candidate.sent == [], "HELLO was spent on an abandoned session"
        assert rrc_client._link is None
        assert g.welcomed == [], g.welcomed
    finally:
        restore()
    print("ok test_disconnect_while_linking_tears_down_the_pending_candidate")


def test_disconnect_after_the_link_goes_active_leaves_no_live_link():
    # Honest about what this one is: a GUARD, not a regression witness. By
    # the time the task reaches the identify checkpoint, disconnect() has
    # already cleared and torn down _link, so this stays green with the
    # _stale() check there removed. It pins the composite invariant the
    # other two do not reach -- an ACTIVE, identified link plus a backout
    # still ends with no live link, no HELLO and no WELCOME at the GUI --
    # and it is the test that would catch _link not being cleared.
    g = _idle_session()
    restore, transport = _install_session_stubs()
    try:
        coro = rrc_client._session_task(b"\x42" * 16)
        _step(coro, 2)
        link = _FakeOutgoingLink.instances[0]
        link.status = _FakeOutgoingLink.ACTIVE
        _step(coro)                       # out of the wait, identify, sleep
        assert link.identified is not None, "the task never identified"
        assert rrc_client._link is link
        rrc_client.disconnect()
        assert _step(coro, 60), "the session task kept running after disconnect"
        assert link.torn_down is True
        assert link.sent == [], "HELLO was sent after the user backed out"
        assert rrc_client._link is None
        assert g.welcomed == [], g.welcomed
    finally:
        restore()
    print("ok test_disconnect_after_the_link_goes_active_spends_no_hello")


def test_every_interface_switch_clears_the_hub_list():
    # Important 8: clear_hubs() existed with the docstring "Interface
    # switched -- reachability changed, start over", and ui.clear_rrc_hubs()
    # existed to serve it, but neither interface-switch path in
    # tdeck_node.py called it. After a LoRa<->TCP switch the RRC tab kept
    # unreachable hubs, and clicking one cost a path request plus a 30 s
    # wait. tdeck_node.py cannot be imported on the host (machine, board,
    # the radio), so this pins the wiring at the source level -- and pins it
    # to the SIBLING calls, so a third switch path added later has to bring
    # the hub list with it too.
    src = open(os.path.join(REPO, "tdeck_node.py")).read()
    sites = src.count("rnsh_client.clear_nodes()")
    assert sites == 2, sites
    assert src.count("nomad_browser.clear_nodes()") == sites
    assert src.count("rrc_client.clear_hubs()") == sites, \
        "an interface switch does not clear the RRC hub list"
    for chunk in src.split("rnsh_client.clear_nodes()")[1:]:
        assert chunk.lstrip().startswith("rrc_client.clear_hubs()"), chunk[:120]
    print("ok test_every_interface_switch_clears_the_hub_list")


def test_clear_hubs_reaches_the_gui():
    g = _reset()
    rrc_client.clear_hubs()
    assert g.cleared == 1, g.cleared
    print("ok test_clear_hubs_reaches_the_gui")


def test_drop_link_does_not_paint_an_abandoned_link_as_a_dropped_one():
    # _drop_link clears _link before teardown() so the synchronous closed
    # callback cannot report a deliberate abandon as "!! link closed" --
    # the same ordering disconnect() relies on.
    g, link = _session()
    rrc_client._drop_link(link)
    assert link.torn_down is True
    assert rrc_client._link is None
    assert not [ln for ln in g.lines if ln[0] == "error"], g.lines
    print("ok test_drop_link_does_not_paint_an_abandoned_link_as_a_dropped_one")


if __name__ == "__main__":
    for name in list(globals()):
        if name.startswith("test_"):
            globals()[name]()
    print("all rrc_client tests passed")
