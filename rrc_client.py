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
    global _gui, _my_identity
    _gui = gui
    _my_identity = identity
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
