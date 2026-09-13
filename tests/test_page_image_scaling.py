# Inline page images must keep their aspect ratio and never be upscaled past
# their native size. The native decoder (webp_fast) STRETCHES to whatever
# target_w x target_h it is given, so ui.py must compute an aspect-preserving,
# down-only target itself. Run:  python3 tests/test_page_image_scaling.py

import os
import sys
import types
import time as _time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_time.ticks_ms = lambda: int(_time.time() * 1000)
_time.ticks_diff = lambda a, b: a - b
_time.sleep_ms = lambda ms: None
sys.modules.setdefault("uasyncio", types.ModuleType("uasyncio"))


class _Pin:
    IN = OUT = PULL_UP = IRQ_FALLING = IRQ_RISING = 0

    def __init__(self, *a, **k):
        pass

    def irq(self, *a, **k):
        pass

    def value(self, *a):
        return 1


_machine = types.ModuleType("machine")
_machine.Pin = _Pin
sys.modules["machine"] = _machine

# Fake native WebP decoder: records the (target_w, target_h) it is asked for.
_calls = []


def _fake_decode(data, tw=None, th=None):
    _calls.append((tw, th))
    w = tw if tw else 0
    h = th if th else 0
    return (w, h, bytes(2 * max(1, w) * max(1, h)))


_fakewebp = types.ModuleType("webp_fast_xtensawin")
_fakewebp.decode = _fake_decode
sys.modules["webp_fast_xtensawin"] = _fakewebp

import ui

_failures = []


def check(cond, name, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  ->  " + detail))
    if not cond:
        _failures.append(name)


class FakeTFT:
    def text(self, *a, **k):
        pass

    def fill_rect(self, *a, **k):
        pass

    def fill(self, *a, **k):
        pass

    def blit_buffer(self, *a, **k):
        pass


def _mkui():
    g = ui.UI(FakeTFT(), object(), lambda: b"\x00", node_name="test")
    g._screen_on = True
    return g


def _vp8_webp(w, h):
    """Minimal lossy-WebP byte string whose header parses to w x h. The body
    is not a real VP8 frame — only the dimension fields matter here (the fake
    decoder ignores the body)."""
    body = (b"\x00\x00\x00"                    # frame tag (3)
            + b"\x9d\x01\x2a"                  # start code (3)
            + bytes([w & 0xFF, (w >> 8) & 0x3F,
                     h & 0xFF, (h >> 8) & 0x3F])  # 14-bit LE width/height
            + b"\x00" * 8)
    chunk = b"VP8 " + len(body).to_bytes(4, "little") + body
    return b"RIFF" + (len(chunk) + 4).to_bytes(4, "little") + b"WEBP" + chunk


def _box():
    return ui.SBAR_X - 2, (ui.BODY_ROWS - 1) * ui.CHAR_H - 2 * ui.IMG_GAP


def _decode_target(g, data):
    _calls.clear()
    g._page_images[0] = {"_raw": data, "state": "loading", "src": ":/media/x.webp"}
    g._decode_page_image(0)
    assert _calls, "decoder was never called"
    return _calls[0]


def test_img_native_size_parses_real_webp():
    path = os.path.expanduser("~/.nomadnetwork/storage/pages/micropython.webp")
    if not os.path.isfile(path):
        check(ui._img_native_size(_vp8_webp(160, 164)) == (160, 164),
              "native size parses crafted VP8 header (real file absent)")
        return
    data = open(path, "rb").read()
    check(ui._img_native_size(data) == (160, 164),
          "native size parses the real installed webp", repr(ui._img_native_size(data)))


def test_small_image_is_not_upscaled():
    g = _mkui()
    box_w, box_h = _box()
    tw, th = _decode_target(g, _vp8_webp(100, 100))   # fits inside the box
    check((tw, th) == (100, 100),
          "an image smaller than the box is decoded at native size (no upscale)",
          "target=%r box=%r" % ((tw, th), (box_w, box_h)))


def test_tall_image_keeps_aspect_and_is_not_stretched():
    g = _mkui()
    box_w, box_h = _box()
    tw, th = _decode_target(g, _vp8_webp(160, 164))
    check(tw <= box_w and th <= box_h, "scaled image fits the box",
          "target=%r box=%r" % ((tw, th), (box_w, box_h)))
    check(tw < box_w, "not stretched to the full box width",
          "tw=%d box_w=%d" % (tw, box_w))
    check(abs(tw / th - 160 / 164) < 0.03, "aspect ratio preserved",
          "tw/th=%.3f want=%.3f" % (tw / th, 160 / 164))
    check(tw <= 160 and th <= 164, "never upscaled past native",
          "target=%r native=(160,164)" % ((tw, th),))


def test_large_image_downscaled_preserving_aspect():
    g = _mkui()
    box_w, box_h = _box()
    tw, th = _decode_target(g, _vp8_webp(600, 300))   # bigger than the box
    check(tw <= box_w and th <= box_h, "large image fits the box",
          "target=%r box=%r" % ((tw, th), (box_w, box_h)))
    check(abs(tw / th - 600 / 300) < 0.03, "aspect ratio preserved on downscale",
          "tw/th=%.3f want=2.0" % (tw / th))


if __name__ == "__main__":
    for _k, _v in sorted(globals().items()):
        if _k.startswith("test_"):
            _v()
    print("\n%d checks failed" % len(_failures) if _failures
          else "\nall page-image scaling tests passed")
    raise SystemExit(1 if _failures else 0)
