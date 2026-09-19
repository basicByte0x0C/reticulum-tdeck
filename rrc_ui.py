# All RRC drawing and key handling.
#
# ui.py is already ~4,000 lines, so the RRC surfaces live here and ui.py
# gains only the tab constant, two states and a dispatch. Every function
# takes the UI instance and uses its display, font, palette and row
# cache, so the geometry constants stay in one place.
#
# The client interprets structured fields; everything the hub says in
# prose is painted as prose. That is why there is no parser here.

from ui import (BODY_Y, CHAR_H, CHAR_W, COLS, INPUT_Y, SCREEN_W,
                BODY_ROWS, STATE_NODES, STATE_RRC_CHAT, STATE_RRC_ROOMS,
                TAB_RRC, _pad, _ascii)
# SCREEN_H and SEP_Y are unused here.


def open_selected_hub(ui):
    """RRC tab click: connect to the highlighted hub."""
    if not ui._rrc_keys or not (0 <= ui._rrc_idx < len(ui._rrc_keys)):
        return False
    dest = ui._rrc_keys[ui._rrc_idx]
    ui._rrc_lines = []
    ui._rrc_room = None
    ui._rrc_members = 0
    ui.state = STATE_RRC_ROOMS
    if ui.on_rrc_connect:
        ui.on_rrc_connect(dest)
    ui.dirty = True
    return True


def _draw_header(ui, left, right):
    # Cached like draw_browser's header -- the key covers everything the
    # row shows, so a hub-name or status change still repaints it.
    cache_key = left + "\x01" + right
    if ui._cache[1] != cache_key:
        ui._cache[1] = cache_key
        ui.tft.text(ui.font, _pad(""), 0, BODY_Y, ui.DIM_CYAN, ui.BG_DARK)
        ui.tft.text(ui.font, "<", 0, BODY_Y, ui.NEON_GREEN, ui.BG_DARK)
        # left is hub-controlled (announced hub name); _tb() carries it
        # through the same glyph-index path _row() uses, so a kept
        # Cyrillic char doesn't hit tft.text() as a raw non-ASCII str.
        ui.tft.text(ui.font, ui._tb(_ascii(left)[:COLS - len(right) - 3]),
                    CHAR_W, BODY_Y, ui.NEON_CYAN, ui.BG_DARK)
        if right:
            x = (COLS - len(right) - 1) * CHAR_W
            ui.tft.text(ui.font, right, x, BODY_Y, ui.DIM_CYAN, ui.BG_DARK)
    ui.tft.fill_rect(0, BODY_Y + CHAR_H - 1, SCREEN_W, 1, ui.DIM_CYAN)


def _line_color(ui, kind):
    if kind == "error":
        return ui.NEON_MAG
    if kind in ("notice", "event"):
        return ui.DIM_CYAN
    return ui.NEON_CYAN


def _wrap(text, width):
    out = []
    while len(text) > width:
        cut = text.rfind(" ", 0, width)
        if cut <= 0:
            cut = width
        out.append(text[:cut])
        text = text[cut:].lstrip()
    out.append(text)
    return out


def _visible_lines(ui, rows):
    """Flatten scrollback into wrapped display lines, newest last."""
    flat = []
    for kind, nick, text in ui._rrc_lines:
        body = text
        if kind == "msg" and nick:
            body = nick + "> " + text
        elif kind == "action" and nick:
            body = "* " + nick + " " + text
        for piece in _wrap(_ascii(body), COLS):
            flat.append((kind, piece))
    start = max(0, len(flat) - rows - ui._rrc_scroll_chat)
    return flat[start:start + rows]


def draw_rooms(ui):
    """Hub console: the MOTD and any /list reply, verbatim."""
    _draw_header(ui, ui._rrc_hub_name or "connecting...",
                 ui._rrc_status[:10] if ui._rrc_status else "")
    rows = BODY_ROWS - 1
    lines = _visible_lines(ui, rows)
    for i in range(rows):
        y = BODY_Y + (i + 1) * CHAR_H
        if i < len(lines):
            kind, text = lines[i]
            ui._draw_row_cached(i + 2, _pad(text), y, _line_color(ui, kind))
        else:
            ui._draw_row_cached(i + 2, "", y, ui.NEON_CYAN)
    _draw_footer(ui)


def _draw_footer(ui):
    if ui._rrc_prompt:
        ui.tft.text(ui.font, _pad("room> " + ui._rrc_input)[:COLS], 0, INPUT_Y,
                    ui.NEON_CYAN, ui.BG_DARK)
        return
    hint = "(b)ack (j)oin  click=open"
    ui.tft.text(ui.font, _pad(hint), 0, INPUT_Y, ui.DIM_CYAN, ui.BG_DARK)


def handle_key(ui, ch, key):
    if ui.state == STATE_RRC_CHAT:
        # alt+w toggles the member panel. A bare (w) cannot: every letter
        # key belongs to the composer, and enter sends it. How alt arrives
        # from the tca8418 driver (esc-prefixed, here) is unverified --
        # Task 10's hardware boot-test is the check; if the driver reports
        # it differently this is a one-line fix.
        if key == b"\x1bw" or key == b"\x1bW":
            ui._rrc_panel = not ui._rrc_panel
            ui._rrc_panel_idx = 0
            ui._rrc_panel_scroll = 0
            ui.dirty = True
            return True
        if ui._rrc_panel:
            if ch == 27:
                ui._rrc_panel = False
                ui.dirty = True
                return True
            return True          # panel holds focus; keys do not compose
    if ui._rrc_prompt:
        return _handle_prompt_key(ui, ch, key)
    if ui.state == STATE_RRC_ROOMS:
        if ch in (ord("j"), ord("J")):
            ui._rrc_prompt = True
            ui._rrc_input = ""
            ui.dirty = True
            return True
        if ch in (ord("b"), ord("B")):
            if ui.on_rrc_disconnect:
                ui.on_rrc_disconnect()
            ui.state = STATE_NODES
            ui.node_tab = TAB_RRC
            ui.dirty = True
            return True
    if ui.state == STATE_RRC_CHAT:
        if ch == 13:                     # enter sends the composer line
            text = ui._rrc_input.strip()
            ui._rrc_input = ""
            ui.dirty = True
            if text and ui.on_rrc_say:
                ui.on_rrc_say(text)
            return True
        if ch == 8:
            ui._rrc_input = ui._rrc_input[:-1]
            ui.dirty = True
            return True
        if ch == 27:                     # esc leaves the room, keeps the link
            if ui.on_rrc_part:
                ui.on_rrc_part()
            ui.state = STATE_RRC_ROOMS
            ui._rrc_panel = False
            ui.dirty = True
            return True
        if 32 <= ch < 127 and len(ui._rrc_input) < COLS - 2:
            ui._rrc_input += chr(ch)
            ui.dirty = True
            return True
    return False


def _handle_prompt_key(ui, ch, key):
    if ch == 13:                     # enter
        text = ui._rrc_input.strip()
        ui._rrc_prompt = False
        ui._rrc_input = ""
        ui.dirty = True
        if text and ui.on_rrc_join:
            room, _, room_key = text.partition(" ")
            ui.on_rrc_join(room, room_key.strip() or None)
        return True
    if ch == 8:                      # backspace
        ui._rrc_input = ui._rrc_input[:-1]
        ui.dirty = True
        return True
    if ch == 27:                     # esc
        ui._rrc_prompt = False
        ui._rrc_input = ""
        ui.dirty = True
        return True
    if 32 <= ch < 127 and len(ui._rrc_input) < COLS - 6:
        ui._rrc_input += chr(ch)
        ui.dirty = True
        return True
    return False


# Focused member panel (alt+w). Drawn with graphics primitives -- fill_rect
# for the body, drawn rules for the frame, SEL_BG strips for the title and
# footer, a selection fill plus a magenta accent bar, and a track/thumb
# scrollbar -- never box-drawing characters: the app has a display driver,
# so the panel looks like the rest of the UI rather than ASCII art.
#
# Geometry is derived from the constants this module already imports from
# ui (SCREEN_W, CHAR_W, CHAR_H), which are themselves board-aware (the Pro
# is 30 columns and a different screen height) -- so nothing here hardcodes
# the v1's 320x240. The row/height counts below (7 rows, 158px) are sized
# for the v1's panel and verified against the host harness's bounds check;
# the Pro gets a boot-test pass in Task 10.
PANEL_X = 8
PANEL_W = SCREEN_W - 16
PANEL_Y = 58
PANEL_H = 158
PANEL_ROWS = 7
_PANEL_TEXT_X = PANEL_X + 8
_PANEL_HASH_X = PANEL_X + PANEL_W - 8 - 10 * CHAR_W


def draw_member_panel(ui):
    """Focused, scrollable member list drawn with graphics primitives.

    Rows are "nick or ?" plus a right-aligned [hash8], the same shape
    _draw_list_rows() uses for peers and hubs. No op/voice markers: rrcd
    exposes no member status to clients, so a "@"/"+" marker would be
    inventing data the protocol does not carry.
    """
    roster = ui._rrc_roster
    ui.tft.fill_rect(PANEL_X, PANEL_Y, PANEL_W, PANEL_H, ui.BG_DARK)
    for i in range(2):               # 2px frame
        ui.tft.fill_rect(PANEL_X + i, PANEL_Y + i, PANEL_W - 2 * i, 1, ui.NEON_CYAN)
        ui.tft.fill_rect(PANEL_X + i, PANEL_Y + PANEL_H - 1 - i,
                         PANEL_W - 2 * i, 1, ui.NEON_CYAN)
        ui.tft.fill_rect(PANEL_X + i, PANEL_Y + i, 1, PANEL_H - 2 * i, ui.NEON_CYAN)
        ui.tft.fill_rect(PANEL_X + PANEL_W - 1 - i, PANEL_Y + i, 1,
                         PANEL_H - 2 * i, ui.NEON_CYAN)

    ui.tft.fill_rect(PANEL_X + 2, PANEL_Y + 2, PANEL_W - 4, 18, ui.SEL_BG)
    # Every string this panel draws goes through ui._tb() -- room name and
    # nicks are hub-controlled (K_ROOM / K_NICK) and a kept Cyrillic char
    # must take the glyph-index path, same as the Task 8 header fix; the
    # static labels are wrapped too, for consistency, even though they are
    # only ever ASCII. The [hash8] column below is the one deliberate
    # exception -- see its comment.
    ui.tft.text(ui.font, ui._tb(_ascii(ui._rrc_room or "?")[:14]), _PANEL_TEXT_X,
                PANEL_Y + 3, ui.YELLOW, ui.SEL_BG)
    count = "%d users" % len(roster)
    ui.tft.text(ui.font, ui._tb(count), PANEL_X + PANEL_W - 8 - len(count) * CHAR_W,
                PANEL_Y + 3, ui.DIM_CYAN, ui.SEL_BG)
    ui.tft.fill_rect(PANEL_X + 2, PANEL_Y + 20, PANEL_W - 4, 1, ui.DIM_CYAN)

    top = ui._rrc_panel_scroll
    for i in range(PANEL_ROWS):
        y = PANEL_Y + 22 + i * CHAR_H
        idx = top + i
        if idx >= len(roster):
            break
        src, nick = roster[idx]
        selected = (idx == ui._rrc_panel_idx)
        if selected:
            ui.tft.fill_rect(PANEL_X + 2, y, PANEL_W - 4 - 6, CHAR_H, ui.SEL_BG)
            ui.tft.fill_rect(PANEL_X + 2, y, 3, CHAR_H, ui.NEON_MAG)
        bg = ui.SEL_BG if selected else ui.BG_DARK
        # A member who has never spoken is known only by hash -- the app's
        # existing convention for an unknown name is "?".
        name = _ascii(nick) if nick else "?"
        ui.tft.text(ui.font, ui._tb(name[:20]), _PANEL_TEXT_X, y,
                    ui.YELLOW if selected else ui.NEON_CYAN, bg)
        # [hash8] is hex from bytes.hex() -- always ASCII, so it
        # deliberately skips ui._tb() rather than being left out by
        # oversight.
        ui.tft.text(ui.font, "[" + src.hex()[:8] + "]", _PANEL_HASH_X, y,
                    ui.DIM_CYAN, bg)

    track_y = PANEL_Y + 22
    track_h = PANEL_ROWS * CHAR_H
    ui.tft.fill_rect(PANEL_X + PANEL_W - 6, track_y, 4, track_h, ui.BG_DARK)
    if len(roster) > PANEL_ROWS:
        bar_h = max(6, track_h * PANEL_ROWS // len(roster))
        bar_y = track_y + track_h * top // len(roster)
        ui.tft.fill_rect(PANEL_X + PANEL_W - 6, bar_y, 4, bar_h, ui.NEON_CYAN)

    foot_y = PANEL_Y + PANEL_H - 22
    ui.tft.fill_rect(PANEL_X + 2, foot_y, PANEL_W - 4, 20, ui.SEL_BG)
    ui.tft.text(ui.font, ui._tb("alt+w close"), _PANEL_TEXT_X, foot_y + 2,
                ui.NEON_GREEN, ui.SEL_BG)
    hint = "click = mention"
    ui.tft.text(ui.font, ui._tb(hint), PANEL_X + PANEL_W - 8 - len(hint) * CHAR_W,
                foot_y + 2, ui.DIM_CYAN, ui.SEL_BG)


def panel_click(ui):
    """Trackball click in the panel: insert the mention, close the panel."""
    roster = ui._rrc_roster
    if not roster or not (0 <= ui._rrc_panel_idx < len(roster)):
        return False
    src = roster[ui._rrc_panel_idx][0]
    token = ui.on_rrc_mention(src) if ui.on_rrc_mention else "@" + src.hex()[:8]
    ui._rrc_input += token + " "
    ui._rrc_panel = False
    ui.dirty = True
    return True


def panel_scroll(ui, delta):
    """Move the panel selection by delta rows, clamped to the roster, and
    keep the visible window (ui._rrc_panel_scroll) tracking it. Called for
    both single trackball ticks and multi-tick drains, so delta may be
    more than 1 in either direction."""
    roster = ui._rrc_roster
    if not roster:
        ui._rrc_panel_idx = 0
        ui._rrc_panel_scroll = 0
        return
    ui._rrc_panel_idx = max(0, min(len(roster) - 1, ui._rrc_panel_idx + delta))
    if ui._rrc_panel_idx < ui._rrc_panel_scroll:
        ui._rrc_panel_scroll = ui._rrc_panel_idx
    elif ui._rrc_panel_idx >= ui._rrc_panel_scroll + PANEL_ROWS:
        ui._rrc_panel_scroll = ui._rrc_panel_idx - PANEL_ROWS + 1
    ui.dirty = True


def draw_room(ui):
    """Room scrollback plus the composer."""
    room = ui._rrc_room or "?"
    count = ("%d users" % ui._rrc_members) if ui._rrc_members else "? users"
    name = _ascii(room)[:COLS - len(count) - 2]
    # Cached like _draw_header/draw_browser's header -- the key covers
    # both the room name and the member count, so either changing repaints.
    cache_key = name + "\x01" + count
    if ui._cache[1] != cache_key:
        ui._cache[1] = cache_key
        ui.tft.text(ui.font, _pad(""), 0, BODY_Y, ui.NEON_CYAN, ui.BG_DARK)
        # room is hub-controlled (echoed by the JOIN reply); _tb() carries
        # it through the same glyph-index path _row() uses.
        ui.tft.text(ui.font, ui._tb(name), 0, BODY_Y, ui.NEON_CYAN, ui.BG_DARK)
        ui.tft.text(ui.font, count, (COLS - len(count) - 1) * CHAR_W, BODY_Y,
                    ui.DIM_CYAN, ui.BG_DARK)
    ui.tft.fill_rect(0, BODY_Y + CHAR_H - 1, SCREEN_W, 1, ui.DIM_CYAN)

    rows = BODY_ROWS - 1
    lines = _visible_lines(ui, rows)
    for i in range(rows):
        y = BODY_Y + (i + 1) * CHAR_H
        if i < len(lines):
            kind, text = lines[i]
            ui._draw_row_cached(i + 2, _pad(text), y, _line_color(ui, kind))
        else:
            ui._draw_row_cached(i + 2, "", y, ui.NEON_CYAN)

    if ui._rrc_panel:
        draw_member_panel(ui)          # Task 9

    prompt = "> " + ui._rrc_input
    ui.tft.text(ui.font, _pad(prompt)[:COLS], 0, INPUT_Y,
                ui.DIM_CYAN if ui._rrc_panel else ui.NEON_CYAN, ui.BG_DARK)
