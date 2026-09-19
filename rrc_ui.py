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
