# FR: Inline images from reference NomadNet nodes — metadata-Resource interop

**Status:** OPEN. Diagnosed 2026-09-13. **Revised 2026-09-13** after verifying every
claim against the actual test-rig source: the venv runs **RNS 1.5.4** (not 1.4.3),
and the "wire format" section + **Part B** originally described a metadata field
(`adv.x` = size) that **does not exist** in RNS — corrected below. The inline-image
feature is merged to `master` (T-Deck `reticulum-tdeck` @ 198fa5c) and flashed to the
device, but images served by **reference NomadNet nodes fail to load**. Symptom on the
T-Deck: block shows `loading image...` then `[image failed]`; footer = `no response`.

**Test rig (already set up on this Mac):** reference **NomadNet 1.4.3** running
from `/Users/milen/Documents/Projects/nomadnet/.venv` (`uv run nomadnet`),
serving `~/.nomadnetwork/storage/pages/index.mu` which embeds
`~/.nomadnetwork/storage/pages/micropython.webp` via
`` `(MicroPython logo`w=n`a=c`:/media/micropython.webp) ``. **Reference RNS 1.5.4** is
in the same venv's site-packages (`nomadnet-1.4.3.dist-info`, `rns-1.5.4.dist-info`) —
this is the version whose Resource format urns must match.

---

## Root cause (TWO layers — both must be fixed)

### Layer 1 — the `no response` you see now: missing `"key"`
Reference NomadNet's media handler REQUIRES a `key` field in the request data:
- `nomadnet/Node.py:172` `serve_media`, `:175` `if not "key" in data: return None`
- `nomadnet/ui/textui/Browser.py:997`: its own browser sends `{"path": path, "key": None}`

The T-Deck (`nomad_browser._fetch_image`, `nomad_browser.py:378`) sends
`{"path": media_path}` with **no `key`**, so `serve_media` returns `None` → RNS sends
no response → the T-Deck's `failed_callback` fires → `_result=("fail","no response")`
→ `[image failed]`.

### Layer 2 — why the `key` fix alone is NOT enough: metadata Resources
Reference `serve_media` returns a **file handle + metadata**, not raw bytes:
- `nomadnet/Node.py:216`: `return [open(file_path, "rb"), {"name": file_name.encode("utf-8")}]`
- `RNS/Link.py:839` tests `type(response[0]) == io.BufferedReader`; `:846` builds
  `RNS.Resource(file_handle, self, metadata=metadata, is_response=True, ...)`.
  On the wire the `{"name":…}` dict is **framed and prepended to the payload inside the
  single Resource** (see wire format below) — NOT sent as a separate field, and NOT
  the `umsgpack.packb([request_id, response])` shape urns understands (that is the
  `else` branch at `Link.py:848`, which media never takes).
- Reference requester branches on the metadata flag: `RNS/Link.py:902-903` —
  `if resource.has_metadata: handle_response(rid, resource.data, ..., metadata=resource.metadata)`;
  `:912` `else: handle_response(rid, response_data, ...)` after `umsgpack.unpackb`.

**urns has no metadata-Resource support** (confirmed):
- `vendor/uP-reticulum/firmware/urns/resource.py` flags are only
  `FLAG_ENCRYPTED/COMPRESSED/IS_RESPONSE` (`0x01/0x02/0x10`, lines 62-64) — no metadata
  flag, no `has_metadata` attribute.
- `vendor/uP-reticulum/firmware/urns/link.py` `resource_concluded` (~1267-1289)
  **always** does `umsgpack.unpackb(resource.data)` expecting `[rid, data]`. On a
  metadata Resource, `resource.data` (post decrypt / strip-random / decompress) is
  `[3-byte len][msgpack(meta)][raw webp]`, so the unpack yields garbage/throws →
  `_fail_request(rid, "malformed response")`.

So even after Layer 1, a reference media response fails as `malformed response`.

This is the **"unverified against reference nodes" risk flagged in the original
plan** (`docs/superpowers/plans/2026-09-13-nomad-inline-images.md`, Design
Decision 5 + Task 10). Our own `example_nomadnet_node.py` returns raw `f.read()`
bytes and never checks `key`, which is why design/host-tests looked fine.

---

## Reference RNS 1.5.4 metadata wire format (what urns must match)
From `RNS/Resource.py` in the test venv — READ THESE before implementing. **The key
correction vs. the first draft of this FR: there is NO `x` size key in the
advertisement. `has_metadata` is a single flag bit, and the metadata's size lives as a
3-byte prefix inside the payload data, discovered only after hash verification.**

- **Framing** (`__init__` ~257-272): with metadata,
  `packed = umsgpack.packb(metadata)`; guard `len(packed) <= METADATA_MAX_SIZE`
  (`= 16*1024*1024 - 1 = 0xFFFFFF`, line 121); then
  `self.metadata = struct.pack(">I", len(packed))[1:] + packed`
  (a **3-byte big-endian length** + msgpack of the dict). At send/split (line 338)
  this blob is **prepended to the payload**: `data = self.metadata + resource_data`.
  The prepended whole is what gets hashed, split, and encrypted.
- **Advertisement flag, NOT a size** (`ResourceAdvertisement` ~1291-1372):
  `self.x = resource.has_metadata` (a **bool**), packed into **bit 5 of the flags byte
  `f`**: `self.f = self.x<<5 | self.p<<4 | self.u<<3 | self.s<<2 | self.c<<1 | self.e`
  (line 1318). `pack()` (1329-1349) emits the msgpack dict with keys
  **`t d n h r o i l q f m` only — there is no `x` key**. `unpack()` recovers
  `adv.x = (adv.f >> 5) & 0x01` (line 1372). `accept()` (168-243) then does
  `if adv.x: resource.has_metadata = True else: False` (208-209). **The metadata size
  is never carried in the advertisement.**
- **Receiver strip** (assembly ~686-728): join parts → decrypt → strip random hash →
  decompress → `calculated_hash = full_hash(self.data + self.random_hash)` over the
  **full** reassembled data (metadata blob + payload). Only if the hash matches AND
  `has_metadata` (and `segment_index == 1`): read the size back from the data —
  `n = data[0]<<16 | data[1]<<8 | data[2]` (line 711) — then the payload is
  `data[3+n:]` (line 717). Note reference leaves `self.data` full and strips into a
  local `data`, because `prove()` (`:768`, `proof = full_hash(self.data + self.hash)`)
  must run over the FULL data to match the sender's `expected_proof`
  (`:448`, same full data). **Strip AFTER hash-verify AND AFTER prove().**

---

## The fix

### Part A — T-Deck (`reticulum-tdeck` repo), tiny
`nomad_browser.py` `_fetch_image` (`:378`): add `key` to the request payload:
```python
rid = _link.request("/media", data={"path": media_path, "key": None},
                    response_callback=_on_response, failed_callback=_on_req_failed,
                    progress_callback=_on_progress, max_response_size=MAX_IMAGE_BYTES)
```
(`None` matches reference `Browser.py:997`; `serve_media` only checks presence.)

### Part B — urns (do UPSTREAM in `varna9000/micropython-reticulum`, then bump the
submodule pin — NEVER fork urns files inside reticulum-tdeck; see memory
`project_transport_fixes`). Add metadata-Resource support. **This is smaller than the
original plan implied: urns already emits/reads the exact same 11-key advertisement as
reference and already reads `adv["f"]`, so NO advertisement-schema change is needed —
just read flag bit 5 and strip a 3-byte-prefixed blob.**

1. `urns/resource.py`: add `FLAG_HAS_METADATA = 0x20`. Default `self.has_metadata =
   False` in `__init__`/reset. In `accept` (right after `r.flags = adv["f"]`, ~line
   208) set `r.has_metadata = bool(adv["f"] & FLAG_HAS_METADATA)`. **Do not** read an
   `adv["x"]` / size key — there is none (a `KeyError` on urns↔urns resources).
2. `urns/resource.py` finalize (`_resource_finalize`, verify at line 592, `self.prove()`
   at 600, `self._conclude()` at 606): leave verify and prove UNCHANGED (proof must be
   over the full metadata+payload). Insert the strip **between `prove()` and
   `_conclude()`**:
   ```python
   if self.has_metadata:
       n = (self.data[0] << 16) | (self.data[1] << 8) | self.data[2]
       self.data = self.data[3 + n:]   # drop 3-byte BE len + msgpack(meta) blob
   ```
   (The T-Deck only needs the raw payload; parsing the `{"name":…}` dict is optional.)
3. `urns/link.py` `resource_concluded` (~1267-1289): if the response resource
   `has_metadata` → `self._dispatch_response(rid, resource.data)` directly (data is
   already the raw payload after step 2); else keep the current
   `umsgpack.unpackb(resource.data)[1]` path. Large NON-metadata page responses stay
   `has_metadata=False` and keep working unchanged.
4. Add host tests in `firmware/tests/` that build a metadata Resource (flag bit 5 set,
   payload = `struct.pack(">I", len(packb(meta)))[1:] + packb(meta) + raw`) and assert
   accept→verify→prove→strip yields the raw payload and `resource_concluded` dispatches
   it; and that a normal `[rid,data]` resource (bit 5 clear) still dispatches `data`.
   Match reference bytes where feasible.

**Single-segment caveat:** urns accepts only single-segment resources and cancels any
whose `total_data_size > MAX_RESOURCE_SIZE` (16384 B — `resource.py:229`). Reference
prepends the metadata blob only on `segment_index == 1`, which is the sole segment urns
accepts, so the 3-byte prefix is always at the front of urns's reassembled data. The
webp images (~14 KB) fit; the `{"name":…}` blob + 3-byte prefix count toward the 16 KB.
Images larger than 16 KB would need multi-segment support (a separate gap, unrelated to
metadata).

### Part C — rebuild + reflash (M5Launcher device)
- `bash tools/build_firmware.sh` → new `tools/firmware_build/micropython.bin`.
  **Check size ≤ 2,097,152 bytes** (the Launcher's `tdeckf` partition). Current
  build is ~2,068,224 B (~28 KB headroom) — the urns change is small (one flag const,
  ~4 lines in finalize, ~3 lines in link) but re-check; if it exceeds the cap, the
  messenger won't fit at `0x1a0000`.
- Flash with `tools/launcher_flash_messenger.sh` (openocd `20260304`, offset
  `0x1a0000`, verify). Confirm layout first with `tools/launcher_dump_parttable.sh`.
  Flash writes are permission-blocked for the assistant → USER runs
  `! bash tools/launcher_flash_messenger.sh`. Power-cycle after (unplug/replug).
  (These two `launcher_*` scripts are untracked in the T-Deck repo.)

---

## Verification
1. Reference node is already serving on this Mac (NomadNet 1.4.3 / RNS 1.5.4, page +
   webp above). From the T-Deck NET tab, open that node, scroll to the image.
   - `no response` = Part A not applied; `malformed response` = Part B not applied /
     metadata not stripped; image renders inline = fixed.
   - If the image renders but the reference node logs a rejected/again-requested
     response resource, the strip ran BEFORE `prove()` — move it after (step B.2).
2. Cross-check (isolates decode/render from interop): our
   `example_nomadnet_node.py` serves raw-bytes `/media` (no key/metadata) — a
   simpler target that should work with Part A alone.

## Facts / gotchas for a fresh session
- **Version:** the serving stack is **NomadNet 1.4.3 on RNS 1.5.4**. Match the 1.5.4
  Resource format (flag bit 5 + 3-byte in-payload length prefix). Metadata Resources
  did not exist in older RNS — do not port from a 1.4.x reading.
- Device runs **M5Launcher**: flash the messenger at **`0x1a0000`** (tdeckf/ota_0),
  NEVER `0x10000` (that's the Launcher). `flash_now.sh`/`flash_tdeck.sh` are the
  vanilla `0x10000` scripts — DO NOT use them. See memory `project_tdeck_v1_launcher_layout`,
  `project_tdeck_flashing`.
- T-Deck decodes WebP via built-in `webp_fast_xtensawin` (proven by the chat-image
  viewer); decode is NOT the problem here.
- Current T-Deck inline-image code (all on `master` @ 198fa5c): `micron.py` `` `( ``
  parse; `nomad_browser.py` `_fetch_image`/`_auto_fetch_task`/`fetch_page_image`;
  `ui.py` `_expand_image_blocks`/`_draw_image_row`/`_decode_page_image`/`page_image_loaded`.
- The manifest freezes urns from `vendor/uP-reticulum/firmware/urns` and the app
  from repo root (`tools/tdeck_manifest.py`), so a rebuild ships both.
