# RRC client controller for the T-Deck.
#
# Owns hub discovery (announces on the "rrc.hub" destination), the
# outgoing link to the hub in use, the RRC session state machine, and one
# joined room. The UI layer (rrc_ui.py, driven by ui.py) renders; the
# wiring in tdeck_node.py connects gui.on_rrc_* to the functions here.
#
# Connect flow (on-demand session):
#   recall identity -> ensure path -> OutgoingLink -> wait ACTIVE ->
#   link.identify() -> HELLO -> await WELCOME -> READY
#
# HELLO is sent exactly once per link. A second HELLO is a session reset
# server-side, so link retries re-establish the link and never re-send it.
#
# Only one session is active at a time, like nomad_browser and rnsh_client.

import time

import rrc_cbor as cbor
import rrc_proto as P

MAX_HUBS = 16

_gui = None
_my_identity = None


# --- discovery --------------------------------------------------------------

def init(gui, identity):
    """Hook announce observation. Call once at boot, after Reticulum init."""
    global _gui, _my_identity, _no_retry, _backoff_until
    _gui = gui
    _my_identity = identity
    _no_retry = False
    _backoff_until = 0
    from urns.transport import Transport
    Transport.register_announce_handler(_on_announce)


def _hub_hash(dest_hash):
    """The "rrc.hub" destination hash for the identity behind dest_hash."""
    from urns.identity import Identity
    from urns.destination import Destination
    data = Identity.known_destinations.get(dest_hash)
    if data and data[2]:
        id_hash = Identity.truncated_hash(data[2])
        return Destination.hash(id_hash, P.HUB_APP, P.HUB_ASPECT)
    return None


def _node_hops(dest_hash):
    try:
        from urns.transport import Transport
        from urns import const as _uc
        entry = Transport.path_table.get(dest_hash)
        if entry:
            return entry[_uc.IDX_PT_HOPS]
    except Exception:
        pass
    return None


def _on_announce(dest_hash, app_data, packet):
    """Transport announce observer -- collect "rrc.hub" announces.

    The hub's app_data is CBOR {"proto":"rrc","v":1,"hub":<name>}; a hub
    that sends none, or sends something we cannot read, is still a hub
    and still gets listed, just without a display name."""
    if _hub_hash(dest_hash) != dest_hash:
        return
    name = None
    if app_data:
        try:
            info = cbor.loads(app_data)
            if isinstance(info, dict) and info.get("proto") == "rrc":
                got = info.get("hub")
                if isinstance(got, str):
                    name = got
        except Exception:
            pass
    if _gui is not None:
        _gui.add_rrc_hub(dest_hash, name=name, hops=_node_hops(dest_hash))
        if _gui._wake_mode == 1:      # announces wake only under Wake: all
            _gui.wake_screen()


# The RRC tab is session-scoped and deliberately NOT seeded from
# Identity.known_destinations, for the same reason the SSH tab isn't: a
# hub recorded days ago says nothing about whether it is reachable now.


def clear_hubs():
    """Interface switched -- reachability changed, start over."""
    if _gui is not None:
        _gui.clear_rrc_hubs()


def _status(text):
    if _gui is not None:
        _gui.rrc_status(text)


# --- session state ----------------------------------------------------------

IDLE = 0
CONNECTING = 1
READY = 2          # linked, identified, welcomed; no room joined
JOINED = 3
CLOSED = 4

SEEN_IDS = 60           # K_IDs remembered for replay dedupe
RATE_BACKOFF_S = 15     # pause sends after a hub "rate limited"

_link = None
_dest = None
_state = IDLE
_room = None
_roster = {}            # identity_hash -> nick or None
_seen_ids = []          # recent K_IDs, newest last
_limits = {}            # WELCOME limits map
_hub_name = None
_no_retry = False       # set by ERROR "banned": never reconnect this session
_backoff_until = 0      # time.time() before which sends are refused


def is_active():
    return _state in (CONNECTING, READY, JOINED)


def _send_env(env):
    if _link is None:
        return False
    try:
        _link.send(cbor.dumps(env))
        return True
    except Exception as e:
        _status("send failed: " + str(e))
        return False


def _nick():
    return P.normalize_nick(getattr(_gui, "node_name", None),
                            _limits.get(P.L_MAX_NICK, P.DEFAULT_MAX_NICK))


def _line(kind, nick, text):
    if _gui is not None:
        _gui.rrc_line(kind, nick, text)


def _seen(mid):
    """True if this K_ID was already rendered. History replay hands back
    the originals, so a rejoin must not paint them twice."""
    if not isinstance(mid, (bytes, bytearray)):
        return False
    mid = bytes(mid)
    if mid in _seen_ids:
        return True
    _seen_ids.append(mid)
    if len(_seen_ids) > SEEN_IDS:
        del _seen_ids[0]
    return False


def _remember(src, nick):
    if not isinstance(src, (bytes, bytearray)):
        return
    src = bytes(src)
    if nick:
        _roster[src] = nick
    elif src not in _roster:
        _roster[src] = None


def _roster_changed():
    if _gui is not None:
        _gui.rrc_roster(len(_roster))


def _on_packet(data, packet=None):
    """Inbound link packet -> one decoded envelope, dispatched by type.

    Structured fields only: every NOTICE the hub sends is rendered as
    text rather than parsed, because its wording is an unversioned
    formatting choice that differs between hub implementations."""
    global _state, _limits, _hub_name, _no_retry, _backoff_until

    env = P.parse(data)
    if env is None:
        return
    t = env[P.K_T]
    src = env.get(P.K_SRC)
    nick = env.get(P.K_NICK)
    body = env.get(P.K_BODY)

    if t == P.T_PING:
        _send_env(P.make_envelope(P.T_PONG, src=_my_identity.hash, body=body))
        return

    if t == P.T_PONG:
        return

    if t == P.T_RESOURCE_ENVELOPE:
        # We decline CAP_RESOURCE_ENVELOPE, so a hub should chunk into
        # NOTICEs instead. If one advertises anyway, ignore it: the link
        # refuses the Resource by default and nothing is owed here.
        return

    if t == P.T_WELCOME:
        if isinstance(body, dict):
            _hub_name = body.get(P.B_WELCOME_HUB)
            limits = body.get(P.B_WELCOME_LIMITS)
            _limits = limits if isinstance(limits, dict) else {}
        else:
            _hub_name = None
            _limits = {}
        _state = READY
        if _gui is not None:
            _gui.rrc_welcome(_hub_name)
        return

    if t == P.T_JOINED:
        if nick:
            _remember(src, nick)
        if isinstance(body, list):
            if _state != JOINED:
                # Our own JOIN reply: the body is the whole member list.
                _roster.clear()
                for member in body:
                    _remember(member, None)
            else:
                for member in body:
                    _remember(member, nick)
        if _state != JOINED:
            _state = JOINED
            if _gui is not None:
                _gui.rrc_joined(env.get(P.K_ROOM))
        elif nick:
            _line("event", None, "* " + nick + " joined")
        _roster_changed()
        return

    if t == P.T_PARTED:
        if isinstance(body, list):
            for member in body:
                if isinstance(member, (bytes, bytearray)):
                    _roster.pop(bytes(member), None)
        if nick:
            _line("event", None, "* " + nick + " left")
        _roster_changed()
        return

    if t in (P.T_MSG, P.T_ACTION):
        if _seen(env.get(P.K_ID)):
            return
        _remember(src, nick)
        _line("msg" if t == P.T_MSG else "action", nick,
              body if isinstance(body, str) else "")
        return

    if t == P.T_NOTICE:
        if isinstance(body, str):
            _line("notice", None, body)
        return

    if t == P.T_ERROR:
        text = body if isinstance(body, str) else "error"
        _line("error", None, text)
        if text == P.ERR_BANNED:
            _no_retry = True
            disconnect()
        elif text == P.ERR_RATE_LIMITED:
            _backoff_until = time.time() + RATE_BACKOFF_S
        return

    # Unknown type: a future core message or an extension. Ignore it.


def disconnect():
    """Tear the session down. Task 5 sends PART first when in a room."""
    global _link, _state, _room
    if _link is not None:
        try:
            _link.teardown()
        except Exception:
            pass
    _link = None
    _room = None
    _state = CLOSED
