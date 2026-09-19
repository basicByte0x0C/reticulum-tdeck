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


if __name__ == "__main__":
    for name in list(globals()):
        if name.startswith("test_"):
            globals()[name]()
    print("all rrc_ui tests passed")
