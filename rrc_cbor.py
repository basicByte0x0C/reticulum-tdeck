# Subset CBOR (RFC 8949) for RRC envelopes.
#
# Written in the style of urns/umsgpack.py, which serialises every LXMF
# message on this device: a major-type byte, then struct for the wide
# lengths. Only what RRC needs is here -- an envelope is a flat map of
# small integer keys whose values are ints, byte strings and text.
#
# Decoding is deliberately tolerant. Hubs relay extension keys (>= 64)
# verbatim and a future one may carry a float or a tag, so those are
# consumed and reported as None rather than raised: an unknown key must
# never cost us the message it rode in on.

import struct


def _head(major, n, out):
    m = major << 5
    if n < 24:
        out.append(bytes((m | n,)))
    elif n < 256:
        out.append(bytes((m | 24, n)))
    elif n < 65536:
        out.append(struct.pack(">BH", m | 25, n))
    elif n < 4294967296:
        out.append(struct.pack(">BI", m | 26, n))
    else:
        out.append(struct.pack(">BQ", m | 27, n))


def _enc(obj, out):
    if obj is None:
        out.append(b"\xf6")
    elif obj is True:
        out.append(b"\xf5")
    elif obj is False:
        out.append(b"\xf4")
    elif isinstance(obj, int):
        if obj >= 0:
            _head(0, obj, out)
        else:
            _head(1, -1 - obj, out)
    elif isinstance(obj, (bytes, bytearray)):
        _head(2, len(obj), out)
        out.append(bytes(obj))
    elif isinstance(obj, str):
        b = obj.encode("utf-8")
        _head(3, len(b), out)
        out.append(b)
    elif isinstance(obj, (list, tuple)):
        _head(4, len(obj), out)
        for item in obj:
            _enc(item, out)
    elif isinstance(obj, dict):
        _head(5, len(obj), out)
        for k in obj:
            _enc(k, out)
            _enc(obj[k], out)
    else:
        raise ValueError("cbor: unsupported type")


def dumps(obj):
    out = []
    _enc(obj, out)
    return b"".join(out)


def _arg(buf, i):
    """Value and next index for the argument of the head byte at i."""
    ai = buf[i] & 0x1F
    i += 1
    if ai < 24:
        return ai, i
    if ai == 24:
        return buf[i], i + 1
    if ai == 25:
        return struct.unpack_from(">H", buf, i)[0], i + 2
    if ai == 26:
        return struct.unpack_from(">I", buf, i)[0], i + 4
    if ai == 27:
        return struct.unpack_from(">Q", buf, i)[0], i + 8
    raise ValueError("cbor: bad additional info")


def _dec(buf, i):
    major = buf[i] >> 5
    if major == 7:
        ai = buf[i] & 0x1F
        if ai == 20:
            return False, i + 1
        if ai == 21:
            return True, i + 1
        if ai in (22, 23):
            return None, i + 1
        if ai == 25:
            return None, i + 3      # float16, skipped
        if ai == 26:
            return None, i + 5      # float32, skipped
        if ai == 27:
            return None, i + 9      # float64, skipped
        raise ValueError("cbor: bad simple value")
    n, j = _arg(buf, i)
    if major == 0:
        return n, j
    if major == 1:
        return -1 - n, j
    if major == 2:
        return bytes(buf[j:j + n]), j + n
    if major == 3:
        return bytes(buf[j:j + n]).decode("utf-8"), j + n
    if major == 4:
        out = []
        for _ in range(n):
            v, j = _dec(buf, j)
            out.append(v)
        return out, j
    if major == 5:
        out = {}
        for _ in range(n):
            k, j = _dec(buf, j)
            v, j = _dec(buf, j)
            out[k] = v
        return out, j
    if major == 6:
        return _dec(buf, j)         # tag: hand back the tagged item
    raise ValueError("cbor: bad major type")


def loads(data):
    value, _ = _dec(memoryview(data), 0)
    return value
