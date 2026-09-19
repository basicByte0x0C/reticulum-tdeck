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


if __name__ == "__main__":
    for name in list(globals()):
        if name.startswith("test_"):
            globals()[name]()
    print("all rrc_ui tests passed")
