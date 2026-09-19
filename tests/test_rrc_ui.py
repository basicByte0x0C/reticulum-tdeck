# Host tests for the RRC tab bar and screens, drawn against the recording
# fake display used by the other ui tests. Run:
#   /opt/homebrew/bin/python3 tests/test_rrc_ui.py

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, REPO)

from test_ui_shell import _mkui as make_ui      # reuse the fake-display bootstrap

import ui as U


def _painted(calls):
    """Flatten FakeTFT's recorded text draws into one comparable string.

    Rows drawn through ui._row()/_draw_row_cached() are glyph-index bytes
    (see ui.UI._tb) while header/footer/tab-bar text is drawn as plain str
    -- draw_node_list() and draw_rooms() mix both in the same frame, so a
    naive str.join over g.tft.calls raises TypeError. test_ui_browser.py
    hits the same split and resolves it the same way: decode the bytes
    calls and join everything as text.
    """
    parts = []
    for c in calls:
        if c[0] != "text":
            continue
        s = c[1]
        if isinstance(s, (bytes, bytearray)):
            s = s.decode("latin-1")
        parts.append(s)
    return " ".join(parts)


def test_tab_bar_fits_forty_columns():
    g = make_ui()
    g.node_tab = U.TAB_RRC
    labels = g._tab_labels()
    assert sum(len(x) for x in labels) <= U.COLS, labels
    assert len(labels) == U.N_TABS == 4
    print("ok test_tab_bar_fits_forty_columns")


def test_tab_bar_compresses_on_a_narrow_panel():
    g = make_ui()
    saved = U.COLS
    try:
        U.COLS = 30
        labels = g._tab_labels()
        assert sum(len(x) for x in labels) <= 30, labels
        assert "RNSH" not in "".join(labels), labels
    finally:
        U.COLS = saved
    print("ok test_tab_bar_compresses_on_a_narrow_panel")


def test_hub_list_holds_sixteen_and_drops_the_oldest():
    g = make_ui()
    for i in range(20):
        g.add_rrc_hub(bytes([i]) * 16, name="hub%d" % i, hops=1)
    assert len(g._rrc_keys) == 16
    # The backing dict must shrink with the key list, or it leaks.
    assert len(g.rrc_hubs) == 16
    # Oldest four evicted, newest retained -- not merely "some sixteen".
    assert bytes([0]) * 16 not in g.rrc_hubs
    assert bytes([3]) * 16 not in g.rrc_hubs
    assert bytes([4]) * 16 in g.rrc_hubs
    assert bytes([19]) * 16 in g.rrc_hubs
    assert g._rrc_keys[0] == bytes([4]) * 16
    assert g._rrc_keys[-1] == bytes([19]) * 16
    print("ok test_hub_list_holds_sixteen_and_drops_the_oldest")


def test_rrc_line_appends_to_scrollback_and_is_bounded():
    g = make_ui()
    for i in range(200):
        g.rrc_line("msg", "sam", "line %d" % i)
    assert len(g._rrc_lines) <= U.RRC_SCROLLBACK
    assert g._rrc_lines[0][2] == "line 80"
    assert g._rrc_lines[-1][2] == "line 199"
    print("ok test_rrc_line_appends_to_scrollback_and_is_bounded")


def test_rrc_tab_lists_hubs_like_the_ssh_tab():
    g = make_ui()
    g.state = U.STATE_NODES
    g.node_tab = U.TAB_RRC
    g.add_rrc_hub(b"\x42" * 16, name="Varna Hub", hops=2)
    g.tft.calls = []
    g.draw_node_list()
    painted = _painted(g.tft.calls)
    assert "Varna Hub" in painted, painted
    assert "[42424242]" in painted, painted
    print("ok test_rrc_tab_lists_hubs_like_the_ssh_tab")


def test_room_browser_renders_hub_notices_verbatim():
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    g._rrc_hub_name = "Varna Hub"
    g.rrc_line("notice", None, "Registered public rooms:")
    g.rrc_line("notice", None, "  #varna - Varna mesh, off-grid")
    g.tft.calls = []
    import rrc_ui
    rrc_ui.draw_rooms(g)
    painted = _painted(g.tft.calls)
    assert "Registered public rooms:" in painted, painted
    assert "#varna - Varna mesh, off-grid" in painted, painted
    print("ok test_room_browser_renders_hub_notices_verbatim")


def test_j_opens_the_room_name_prompt():
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    import rrc_ui
    rrc_ui.handle_key(g, ord("j"), b"j")
    assert g._rrc_prompt is True
    print("ok test_j_opens_the_room_name_prompt")


def test_prompt_enter_joins_the_typed_room():
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    joined = []
    g.on_rrc_join = lambda room, key=None: joined.append((room, key))
    import rrc_ui
    rrc_ui.handle_key(g, ord("j"), b"j")
    for ch in "#varna":
        rrc_ui.handle_key(g, ord(ch), ch.encode())
    rrc_ui.handle_key(g, 13, b"\r")
    assert joined == [("#varna", None)], joined
    print("ok test_prompt_enter_joins_the_typed_room")


def test_prompt_splits_a_room_key_on_whitespace():
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    joined = []
    g.on_rrc_join = lambda room, key=None: joined.append((room, key))
    import rrc_ui
    rrc_ui.handle_key(g, ord("j"), b"j")
    for ch in "#secret hunter2":
        rrc_ui.handle_key(g, ord(ch), ch.encode())
    rrc_ui.handle_key(g, 13, b"\r")
    assert joined == [("#secret", "hunter2")], joined
    print("ok test_prompt_splits_a_room_key_on_whitespace")


def test_enter_on_rrc_tab_connects_the_hub_not_a_shell():
    # Carried finding from Task 6: with four tabs, the Enter-key fallback in
    # _handle_key_nodes must not fall through to _open_selected_shell() when
    # the RRC tab is selected. Seed a stale SSH selection too, so a wrong
    # dispatch would be caught red-handed rather than merely no-op-ing on an
    # empty shell list.
    g = make_ui()
    g.state = U.STATE_NODES
    g.node_tab = U.TAB_RRC
    g.add_rrc_hub(b"\x42" * 16, name="Varna Hub", hops=2)
    g._rrc_idx = 0
    g.shell_nodes[b"\xaa" * 16] = {"name": "stale", "hops": 1, "seen": 0}
    g._shell_keys = [b"\xaa" * 16]
    g.ssh_idx = 0
    connected = []
    g.on_rrc_connect = lambda dest: connected.append(dest)
    g.handle_key(b"\r")
    assert connected == [b"\x42" * 16], connected
    assert g.connects == [], g.connects   # on_shell_connect must never fire
    assert g.state == U.STATE_RRC_ROOMS, g.state
    print("ok test_enter_on_rrc_tab_connects_the_hub_not_a_shell")


def test_non_ascii_hub_text_is_transliterated_before_drawing():
    # FakeTFT.text does s.encode("ascii") and the device font has no glyphs
    # beyond ASCII/CP866 either, so an un-_ascii()'d string from a hub is a
    # crash on hardware, not a cosmetic problem. A hub controls its own name
    # and the text of every notice it sends.
    #
    # ui._ascii() keeps ASCII plus a curated Cyrillic range (the CP866 font
    # has those glyphs) and DROPS everything else -- accented Latin, CJK,
    # emoji -- rather than transliterating it (confirmed against ui._ascii
    # and ui._CYR directly, not assumed). Cyrillic itself is not a useful
    # probe for the hub-name line specifically: _draw_header draws
    # _ascii(left) as a plain str without ui._tb(), so a *kept* Cyrillic
    # character still reaches FakeTFT.text() as non-ASCII and raises -- a
    # pre-existing _draw_header gap the coordinator flagged as Task 8's to
    # fix, not evidence that _ascii() didn't run. A dropped-class character
    # (accented Latin) exercises the same _ascii(left) call without
    # tripping that unrelated bug, and a second one in the notice body
    # covers _ascii(body) in _visible_lines.
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    g._rrc_hub_name = "Café Hub"                          # e-acute
    g.rrc_line("notice", None, "room über: тест")  # u-diaeresis + kept Cyrillic
    g.tft.calls = []
    import rrc_ui
    rrc_ui.draw_rooms(g)                     # must not raise
    painted = _painted(g.tft.calls)
    assert "é" not in painted, painted  # the hub name's e-acute
    assert "ü" not in painted, painted  # the notice's u-diaeresis
    assert "Caf Hub" in painted, painted     # the rest of the hub name survives
    print("ok test_non_ascii_hub_text_is_transliterated_before_drawing")


def test_cyrillic_hub_name_survives_the_console_header():
    # _ascii() KEEPS Cyrillic (ui._CYR), unlike the accented latin chars the
    # sibling test above uses -- so this is the path that must reach _tb().
    # Without the _tb() wrap in _draw_header, the raw str hits tft.text()
    # and FakeTFT's s.encode("ascii") raises; the real driver would
    # silently UTF-8-mangle it instead.
    #
    # The assertion checks for a *bytes* text call specifically, not just
    # "any text call happened": draw_rooms()'s footer always paints
    # something, so a bare any(c[0] == "text" ...) would stay true even if
    # the header swallowed an encode failure and painted nothing. A bytes
    # call can only come out of _tb(), and with an empty scrollback here
    # the header's hub-name draw is the only call that can produce one.
    g = make_ui()
    g.state = U.STATE_RRC_ROOMS
    g._rrc_hub_name = "Варна Хаб"
    g.tft.calls = []
    import rrc_ui
    rrc_ui.draw_rooms(g)                       # must not raise
    assert any(c[0] == "text" and isinstance(c[1], (bytes, bytearray))
               for c in g.tft.calls), g.tft.calls
    print("ok test_cyrillic_hub_name_survives_the_console_header")


def test_cyrillic_room_name_survives_the_room_header():
    # Same shape and same reasoning as the console-header test above, for
    # draw_room()'s own header (the room name is hub-echoed via the JOIN
    # reply's K_ROOM field, so it needs the same treatment as a hub name).
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#варна"
    g._rrc_members = 3
    g.tft.calls = []
    import rrc_ui
    rrc_ui.draw_room(g)                        # must not raise
    assert any(c[0] == "text" and isinstance(c[1], (bytes, bytearray))
               for c in g.tft.calls), g.tft.calls
    print("ok test_cyrillic_room_name_survives_the_room_header")


def test_trackball_scroll_on_rrc_tab_does_not_touch_ssh_state():
    g = make_ui()
    g.state = U.STATE_NODES
    g.node_tab = U.TAB_RRC
    for i in range(4):
        g.add_rrc_hub(bytes([i]) * 16, name="hub%d" % i, hops=1)
    g.add_shell_node(b"\xee" * 16, name="listener", hops=1)
    g.ssh_idx = 0
    g._irq_down = 1; g.handle_trackball()
    assert g._rrc_idx == 1, g._rrc_idx
    assert g.ssh_idx == 0, "scrolling the RRC tab moved the SSH selection"
    print("ok test_trackball_scroll_on_rrc_tab_does_not_touch_ssh_state")


def test_trackball_click_on_rrc_tab_opens_the_hub():
    g = make_ui()
    g.state = U.STATE_NODES
    g.node_tab = U.TAB_RRC
    g.add_rrc_hub(b"\x42" * 16, name="Varna Hub", hops=2)
    g._rrc_idx = 0
    connected = []
    g.on_rrc_connect = lambda dest: connected.append(dest)
    g._irq_click = 1; g.handle_trackball()
    assert connected == [b"\x42" * 16], connected
    assert g.state == U.STATE_RRC_ROOMS, g.state
    print("ok test_trackball_click_on_rrc_tab_opens_the_hub")


def test_delete_on_rrc_tab_forgets_a_hub_not_a_listener():
    g = make_ui()
    g.state = U.STATE_NODES
    g.node_tab = U.TAB_RRC
    g.add_rrc_hub(b"\x11" * 16, name="Varna Hub", hops=1)
    g.add_rrc_hub(b"\x22" * 16, name="KC1AWV Hub", hops=2)
    g.add_shell_node(b"\xee" * 16, name="listener", hops=1)
    g._rrc_idx = 0
    g.delete_selected()
    assert b"\x11" * 16 not in g.rrc_hubs, "selected hub was not forgotten"
    assert len(g._rrc_keys) == 1
    assert b"\xee" * 16 in g.shell_nodes, "deleting a hub removed an SSH listener"
    assert len(g._shell_keys) == 1
    print("ok test_delete_on_rrc_tab_forgets_a_hub_not_a_listener")


def test_room_view_shows_room_and_member_count():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g._rrc_members = 12
    g.tft.calls = []
    import rrc_ui
    rrc_ui.draw_room(g)
    painted = _painted(g.tft.calls)
    assert "#varna" in painted, painted
    assert "12 users" in painted, painted
    print("ok test_room_view_shows_room_and_member_count")


def test_room_view_prefixes_messages_and_marks_actions():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g.rrc_line("msg", "kc1awv", "anyone on 868?")
    g.rrc_line("action", "sam", "waves")
    g.tft.calls = []
    import rrc_ui
    rrc_ui.draw_room(g)
    painted = _painted(g.tft.calls)
    assert "kc1awv> anyone on 868?" in painted, painted
    assert "* sam waves" in painted, painted
    print("ok test_room_view_prefixes_messages_and_marks_actions")


def test_typing_and_enter_sends_through_on_rrc_say():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    said = []
    g.on_rrc_say = lambda text: said.append(text)
    import rrc_ui
    for ch in "gm":
        rrc_ui.handle_key(g, ord(ch), ch.encode())
    rrc_ui.handle_key(g, 13, b"\r")
    assert said == ["gm"], said
    assert g._rrc_input == ""
    print("ok test_typing_and_enter_sends_through_on_rrc_say")


def test_empty_enter_does_not_send():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    said = []
    g.on_rrc_say = lambda text: said.append(text)
    import rrc_ui
    rrc_ui.handle_key(g, 13, b"\r")
    assert said == [], said
    assert g._rrc_input == ""
    print("ok test_empty_enter_does_not_send")


def test_backspace_on_empty_input_is_a_noop():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    import rrc_ui
    rrc_ui.handle_key(g, 8, b"\x08")   # must not raise or go negative
    assert g._rrc_input == ""
    print("ok test_backspace_on_empty_input_is_a_noop")


def test_back_parts_the_room_and_returns_to_the_console():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    parted = []
    g.on_rrc_part = lambda: parted.append(True)
    import rrc_ui
    rrc_ui.handle_key(g, 27, b"\x1b")
    assert parted == [True]
    assert g.state == U.STATE_RRC_ROOMS
    print("ok test_back_parts_the_room_and_returns_to_the_console")


def test_alt_w_opens_the_panel_and_takes_focus():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    import rrc_ui
    rrc_ui.handle_key(g, ord("w"), b"\x1bw")     # alt+w arrives esc-prefixed
    assert g._rrc_panel is True
    # a letter no longer reaches the composer while the panel has focus
    rrc_ui.handle_key(g, ord("x"), b"x")
    assert g._rrc_input == ""
    print("ok test_alt_w_opens_the_panel_and_takes_focus")


def test_panel_lists_nick_or_question_mark_with_hash():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g._rrc_panel = True
    g.rrc_members([(b"\x11" * 16, "sv2rck"), (b"\x22" * 16, None)])
    g.tft.calls = []
    import rrc_ui
    rrc_ui.draw_member_panel(g)
    # Every string but the [hash8] column comes back as _tb() glyph-index
    # bytes now (ruling 2), so this uses the shared _painted() helper --
    # same reasoning as test_room_browser_renders_hub_notices_verbatim and
    # the rest of this file -- rather than a raw str.join over mixed
    # str/bytes calls, which would TypeError before any assertion runs.
    painted = _painted(g.tft.calls)
    assert "sv2rck" in painted, painted
    assert "[11111111]" in painted, painted
    assert "?" in painted, painted
    print("ok test_panel_lists_nick_or_question_mark_with_hash")


def test_panel_wraps_a_cyrillic_nick_through_tb():
    # Nicks are hub-controlled (K_NICK is whatever any peer in the room
    # calls itself) -- attacker-controlled input that lands on screen the
    # instant the panel opens. ui._ascii() KEEPS Cyrillic (ui._CYR) as a
    # keep-filter, so a Cyrillic nick survives it and must go through
    # ui._tb() before tft.text(), exactly like the Task 8 header fix.
    # Without that wrap the raw str hits FakeTFT.text(), which does
    # s.encode("ascii") and raises -- proven RED by temporarily dropping
    # the ui._tb() wrap around the row's name draw (see task-9-report.md).
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g._rrc_panel = True
    g.rrc_members([(b"\x11" * 16, "Варна")])
    g.tft.calls = []
    import rrc_ui
    rrc_ui.draw_member_panel(g)                 # must not raise
    assert any(c[0] == "text" and isinstance(c[1], (bytes, bytearray))
               for c in g.tft.calls), g.tft.calls
    print("ok test_panel_wraps_a_cyrillic_nick_through_tb")


def test_panel_click_inserts_the_mention_and_closes():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g._rrc_panel = True
    g.rrc_members([(b"\x11" * 16, "sv2rck")])
    g.on_rrc_mention = lambda h: "@sv2rck"
    import rrc_ui
    rrc_ui.panel_click(g)
    assert g._rrc_input == "@sv2rck "
    assert g._rrc_panel is False
    print("ok test_panel_click_inserts_the_mention_and_closes")


def test_panel_scroll_clamps_on_an_empty_roster():
    # An empty roster must not push _rrc_panel_idx negative or past the
    # (nonexistent) end -- the next draw_member_panel() dereferences it.
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g._rrc_panel = True
    g.rrc_members([])
    import rrc_ui
    rrc_ui.panel_scroll(g, 1)
    rrc_ui.panel_scroll(g, -1)
    assert g._rrc_panel_idx == 0
    rrc_ui.draw_member_panel(g)                 # must not raise/IndexError
    print("ok test_panel_scroll_clamps_on_an_empty_roster")


def test_panel_scroll_clamps_at_the_roster_ends():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g._rrc_panel = True
    g.rrc_members([(bytes([i]) * 16, "n%d" % i) for i in range(3)])
    import rrc_ui
    for _ in range(5):                          # walk well past the top
        rrc_ui.panel_scroll(g, -1)
    assert g._rrc_panel_idx == 0
    for _ in range(5):                          # walk well past the bottom
        rrc_ui.panel_scroll(g, 1)
    assert g._rrc_panel_idx == 2
    rrc_ui.draw_member_panel(g)                 # must not raise/IndexError
    print("ok test_panel_scroll_clamps_at_the_roster_ends")


def test_panel_scroll_clamps_when_the_roster_is_shorter_than_the_view():
    # Fewer members than PANEL_ROWS: the scroll offset must stay pinned
    # at 0, or a later-arriving roster (which sizes _rrc_panel_idx down
    # via rrc_members()) could leave _rrc_panel_scroll stranded above it.
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g._rrc_panel = True
    g.rrc_members([(b"\x11" * 16, "sv2rck")])
    import rrc_ui
    rrc_ui.panel_scroll(g, 1)
    assert g._rrc_panel_scroll == 0, g._rrc_panel_scroll
    print("ok test_panel_scroll_clamps_when_the_roster_is_shorter_than_the_view")


def test_rrc_members_clamps_panel_idx_when_the_roster_shrinks():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g._rrc_panel = True
    g.rrc_members([(bytes([i]) * 16, "n%d" % i) for i in range(3)])
    g._rrc_panel_idx = 2
    g.rrc_members([(b"\x11" * 16, "sv2rck")])    # roster shrank to one member
    assert g._rrc_panel_idx == 0, g._rrc_panel_idx
    print("ok test_rrc_members_clamps_panel_idx_when_the_roster_shrinks")


def test_roster_shrink_does_not_strand_the_panel_past_the_members():
    # Review finding: the first pass clamped _rrc_panel_scroll against
    # _rrc_panel_idx (the selection) instead of against the panel's actual
    # valid top-of-window range. A mass PART can shrink the roster to fewer
    # members than fit on screen while both idx and scroll are still deep
    # in a long list; clamping scroll to idx alone leaves it stranded above
    # 0 even though every remaining member would now fit from scroll=0.
    # This needs BOTH a stale nonzero scroll and a shrink past it in the
    # same call -- none of the other clamp tests combine those two, which
    # is exactly why this slipped through the first pass.
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g._rrc_panel = True
    big = [(bytes([i]) * 16, "n%d" % i) for i in range(20)]
    g.rrc_members(big)
    g._rrc_panel_idx = 19          # scrolled to the bottom of a long roster
    g._rrc_panel_scroll = 13
    g.rrc_members(big[:6])         # mass PART: 6 members, 7 rows visible
    assert g._rrc_panel_scroll == 0, g._rrc_panel_scroll
    assert g._rrc_panel_idx == 5, g._rrc_panel_idx
    g.tft.calls = []
    import rrc_ui
    rrc_ui.draw_member_panel(g)
    painted = _painted(g.tft.calls)
    for nick in ("n0", "n1", "n2", "n3", "n4", "n5"):
        assert nick in painted, (nick, painted)
    print("ok test_roster_shrink_does_not_strand_the_panel_past_the_members")


def test_draw_room_paints_the_panel_when_it_is_open():
    # Minor 1: every other panel test calls draw_member_panel() directly.
    # draw_room()'s "if ui._rrc_panel: draw_member_panel(ui)" dispatch is
    # the only thing connecting panel-open state to the panel actually
    # painting, and nothing exercised it.
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g._rrc_panel = True
    g.rrc_members([(b"\x11" * 16, "sv2rck")])
    g.tft.calls = []
    import rrc_ui
    rrc_ui.draw_room(g)
    painted = _painted(g.tft.calls)
    assert "sv2rck" in painted, painted
    print("ok test_draw_room_paints_the_panel_when_it_is_open")


def test_trackball_inside_the_panel_scrolls_members_not_scrollback():
    # Full entry point (handle_trackball), not the bare module function --
    # this is the wiring the panel's "takes focus" claim actually rests on.
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g._rrc_panel = True
    g.rrc_members([(bytes([i]) * 16, "n%d" % i) for i in range(3)])
    before_scroll_chat = g._rrc_scroll_chat
    g._irq_down = 1
    g.handle_trackball()
    assert g._rrc_panel_idx == 1, g._rrc_panel_idx
    assert g._rrc_scroll_chat == before_scroll_chat
    print("ok test_trackball_inside_the_panel_scrolls_members_not_scrollback")


def test_trackball_click_inside_the_panel_inserts_mention_via_handle_trackball():
    g = make_ui()
    g.state = U.STATE_RRC_CHAT
    g._rrc_room = "#varna"
    g._rrc_panel = True
    g.rrc_members([(b"\x11" * 16, "sv2rck")])
    g.on_rrc_mention = lambda h: "@sv2rck"
    g._irq_click = 1
    g.handle_trackball()
    assert g._rrc_input == "@sv2rck "
    assert g._rrc_panel is False
    print("ok test_trackball_click_inside_the_panel_inserts_mention_via_handle_trackball")


if __name__ == "__main__":
    for name in list(globals()):
        if name.startswith("test_"):
            globals()[name]()
    print("all rrc_ui tests passed")
