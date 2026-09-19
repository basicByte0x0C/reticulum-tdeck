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
_dest = None            # current hub identity hash being connected to
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
        if _state != JOINED:
            # Our own JOIN reply: the body is the room's entire member
            # list, and there is no nick -- it is not an arrival event.
            _roster.clear()
            if isinstance(body, list):
                for member in body:
                    _remember(member, None)
            _state = JOINED
            if _gui is not None:
                _gui.rrc_joined(env.get(P.K_ROOM))
        else:
            # Somebody else arrived. K_SRC is the hub; the body carries
            # the one identity that actually joined, and K_NICK names it.
            if isinstance(body, list):
                for member in body:
                    _remember(member, nick)
            if nick:
                _line("event", None, "* " + nick + " joined")
        _roster_changed()
        return

    if t == P.T_PARTED:
        # Roster accuracy depends on the hub's include_joined_member_list
        # config: when off, T_PARTED bodies are None and departures linger.
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


# --- outbound ---------------------------------------------------------------

CONNECT_PATH_WAIT = 30   # seconds to wait for a path
LINK_ATTEMPTS = 4        # lossy multi-hop LoRa drops requests and proofs
WELCOME_WAIT = 60        # upper bound on the WELCOME wait

_task_gen = 0


def compose_cap():
    """Longest body this session can send, from the hub's limit and the link."""
    mdu = getattr(_link, "mdu", 431) if _link is not None else 431
    return P.body_cap(mdu, _limits.get(P.L_MAX_BODY))


def mention_for(identity_hash):
    """The @-token that names this member.

    The hub matches @nick or @<6+ hex of an identity hash>; a bare nick
    matches nothing, and an ambiguous @nick resolves to nobody at all.
    We hold the roster, so ambiguity is decidable here."""
    src = bytes(identity_hash)
    nick = _roster.get(src)
    if nick:
        same = 0
        for other in _roster:
            if _roster[other] == nick:
                same += 1
        if same == 1:
            return "@" + nick
    return "@" + src.hex()[:8]


def say(text):
    """Send composer text to the joined room.

    "/me ..." becomes ACTION; every other slash string goes as MSG, which
    is what makes the hub's whole command set free."""
    if _state != JOINED or not text:
        return False
    if time.time() < _backoff_until:
        _status("rate limited - hold on")
        return False
    t = P.T_MSG
    body = text
    if text.startswith("/me ") and len(text) > 4:
        t = P.T_ACTION
        body = text[4:]
    cap = compose_cap()
    if len(body.encode("utf-8")) > cap:
        # cap is a byte budget; trim whole characters until the encoded
        # body fits, so a multi-byte tail cannot overrun it.
        while body and len(body.encode("utf-8")) > cap:
            body = body[:-1]
        if not body:
            return False
    return _send_env(P.make_envelope(t, src=_my_identity.hash, room=_room,
                                     body=body, nick=_nick()))


def join(room, key=None):
    """JOIN a room. A +k room takes its key as the JOIN body."""
    global _room
    if _state not in (READY, JOINED) or not room:
        return False
    name = room.strip().lower()     # rrcd normalises exactly this way
    if not name:
        return False
    _room = name
    _roster.clear()
    return _send_env(P.make_envelope(P.T_JOIN, src=_my_identity.hash,
                                     room=name, body=key, nick=_nick()))


def part():
    """PART the joined room, staying connected to the hub."""
    global _room, _state
    if _state != JOINED or not _room:
        return False
    ok = _send_env(P.make_envelope(P.T_PART, src=_my_identity.hash, room=_room))
    _room = None
    _roster.clear()
    _state = READY
    _roster_changed()
    return ok


def disconnect():
    """Tear the session down: PART the room, then close the link."""
    global _link, _state, _room, _limits, _hub_name
    if _state == JOINED:
        part()
    if _link is not None:
        try:
            _link.teardown()
        except Exception:
            pass
    _link = None
    _room = None
    _limits = {}
    _hub_name = None
    _roster.clear()
    _seen_ids[:] = []
    _state = CLOSED


def _task_gen_next():
    global _task_gen
    _task_gen += 1
    return _task_gen


def _stale(my_gen):
    return my_gen != _task_gen


def connect(dest_hash):
    """GUI: open an RRC session to a hub (RRC tab click / manual hash)."""
    import uasyncio as asyncio
    asyncio.create_task(_session_task(dest_hash))


async def _session_task(dest_hash):
    """Path -> link (with retries) -> identify -> HELLO -> WELCOME.

    HELLO is sent once, after the link is ACTIVE and identified. Retrying
    it on a live link would reset the session server-side and drop us
    from every room, so only link establishment is retried."""
    global _link, _dest, _state, _limits, _no_retry

    import uasyncio as asyncio
    from urns.identity import Identity
    from urns.transport import Transport

    if is_active():
        _status("busy - session in progress")
        return
    if _no_retry:
        _status("banned by this hub")
        return

    my_gen = _task_gen_next()
    _dest = dest_hash
    _state = CONNECTING
    _limits = {}

    try:
        if not Transport.has_path(dest_hash) or Identity.recall(dest_hash) is None:
            _status("finding path...")
            Transport.request_path(dest_hash)
            for _ in range(CONNECT_PATH_WAIT):
                await asyncio.sleep(1)
                if _stale(my_gen):
                    return
                if Transport.has_path(dest_hash) and Identity.recall(dest_hash):
                    break
            else:
                _status("no path to hub")
                _state = CLOSED
                return

        identity = Identity.recall(dest_hash)
        if identity is None:
            _status("unknown identity")
            _state = CLOSED
            return

        from urns.link import OutgoingLink
        from urns.destination import Destination
        dst = Destination(identity, Destination.OUT, Destination.SINGLE,
                          P.HUB_APP, P.HUB_ASPECT)
        hops = max(1, Transport.hops_to(dest_hash))
        per_attempt = min(90, max(40, 14 * hops))
        link = None
        for attempt in range(1, LINK_ATTEMPTS + 1):
            if _stale(my_gen):
                return
            _status("linking..." if attempt == 1
                    else "linking retry %d/%d..." % (attempt, LINK_ATTEMPTS))
            if attempt > 1:
                Transport.request_path(dest_hash)
                await asyncio.sleep(1)
            await asyncio.sleep_ms(50)
            link = OutgoingLink(dst, closed_callback=_on_link_closed,
                                sign_proofs=True)
            _link = link
            t0 = time.time()
            while link.status == OutgoingLink.PENDING and \
                    time.time() - t0 < per_attempt:
                await asyncio.sleep_ms(200)
                if _stale(my_gen):
                    return
            if link.status == OutgoingLink.ACTIVE:
                break
            try:
                link.teardown()
            except Exception:
                pass
            link = None
        if link is None or link.status != OutgoingLink.ACTIVE:
            _status("link failed")
            _state = CLOSED
            return

        _status("identifying...")
        link.set_packet_callback(_on_packet)
        link.identify(_my_identity)
        await asyncio.sleep_ms(200)

        _status("waiting for WELCOME...")
        hello_body = {P.B_HELLO_NAME: "tdeck", P.B_HELLO_VER: "1",
                      P.B_HELLO_CAPS: {}}
        _send_env(P.make_envelope(P.T_HELLO, src=_my_identity.hash,
                                  body=hello_body, nick=_nick()))
        t0 = time.time()
        while _state == CONNECTING and time.time() - t0 < WELCOME_WAIT:
            await asyncio.sleep_ms(250)
            if _stale(my_gen):
                return
        if _state == CONNECTING:
            _status("no WELCOME - hub silent")
            disconnect()
    except Exception as e:
        _status("connect failed: " + str(e))
        _state = CLOSED


def _on_link_closed(link):
    global _state
    if link is _link:
        _state = CLOSED
        _line("error", None, "!! link closed")
        if _gui is not None:
            _gui.rrc_closed()
