"""Stdlib-only PNG decode/encode and Plate Carrée -> Web Mercator reprojection
for the CWA O-A0058-005 radar composite (8-bit RGBA, non-interlaced).

Longitude is linear in both projections, so reprojection only remaps rows:
each output row is a copy of the source row at the matching latitude
(nearest neighbour). Leaflet stretches an ImageOverlay linearly in Mercator
space between its lat/lon bounds, so the output keeps the same bounds.

Row filters are undone with whole-row big-integer arithmetic (SWAR), so the
common None/Sub/Up rows never loop per byte in Python. Paeth rows loop only
over the bytes near radar echo; Average rows (not used by CWA today) fall
back to a plain loop.
"""
import math
import re
import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
BYTES_PER_PIXEL = 4  # RGBA, 8 bits per channel


class PNGError(ValueError):
    """The PNG is malformed or not in the supported format."""


def _lane_masks(n: int) -> tuple[int, int, int]:
    high = int.from_bytes(b"\x80" * n, "little")
    low = int.from_bytes(b"\x7f" * n, "little")
    full = (1 << (8 * n)) - 1
    return high, low, full


def _add_bytes(a: int, b: int, high: int, low: int) -> int:
    """Byte-wise (a + b) mod 256 on integers packing one byte per lane."""
    return ((a & low) + (b & low)) ^ ((a ^ b) & high)


def _sub_bytes(a: int, b: int, high: int, low: int) -> int:
    """Byte-wise (a - b) mod 256 on integers packing one byte per lane."""
    return ((a | high) - (b & low)) ^ ((a ^ b ^ high) & high)


def _unfilter_sub(row: bytes, masks) -> bytes:
    high, low, full = masks
    value = int.from_bytes(row, "little")
    shift = BYTES_PER_PIXEL
    while shift < len(row):  # prefix sum per channel lane, doubling the stride
        value = _add_bytes(value, (value << (8 * shift)) & full, high, low)
        shift *= 2
    return value.to_bytes(len(row), "little")


def _unfilter_up(row: bytes, prev: bytes, masks) -> bytes:
    high, low, _ = masks
    value = _add_bytes(int.from_bytes(row, "little"), int.from_bytes(prev, "little"), high, low)
    return value.to_bytes(len(row), "little")


def _unfilter_average(row: bytes, prev: bytes) -> bytes:
    out = bytearray(row)
    for i in range(len(out)):
        left = out[i - BYTES_PER_PIXEL] if i >= BYTES_PER_PIXEL else 0
        out[i] = (out[i] + ((left + prev[i]) >> 1)) & 0xFF
    return bytes(out)


_NONZERO_RUN = re.compile(rb"[^\x00]+")


def _unfilter_paeth(row: bytes, prev: bytes, masks) -> bytes:
    if not any(prev):  # Paeth(a, 0, 0) == a, i.e. the Sub filter
        return _unfilter_sub(row, masks)
    bpp = BYTES_PER_PIXEL
    n = len(row)
    # Where the filtered byte, the byte above and the byte above-left are all
    # zero, Paeth predicts the left byte, so the output repeats the previous
    # pixel. Only bytes outside such stretches need the per-byte loop.
    active = int.from_bytes(row, "little") | int.from_bytes(prev, "little")
    active |= (active << (8 * bpp)) & ((1 << (8 * n)) - 1)
    out = bytearray(n)
    done = 0  # out[:done] is final
    for match in _NONZERO_RUN.finditer(active.to_bytes(n, "little")):
        start = match.start() - match.start() % bpp
        end = min(n, -(-match.end() // bpp) * bpp)
        if start > done:  # quiet stretch: repeat the last pixel
            last = out[done - bpp:done] if done else bytes(bpp)
            out[done:start] = last * ((start - done) // bpp)
        start = max(start, done)
        for i in range(start, end):
            if i >= bpp:
                a, c = out[i - bpp], prev[i - bpp]
            else:
                a = c = 0
            b = prev[i]
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
            out[i] = (row[i] + pred) & 0xFF
        done = end
    if done < n:
        last = out[done - bpp:done] if done else bytes(bpp)
        out[done:] = last * ((n - done) // bpp)
    return bytes(out)


def decode_rgba(data: bytes) -> tuple[int, int, list[bytes]]:
    """Return (width, height, rows) of an 8-bit RGBA non-interlaced PNG."""
    if not data.startswith(PNG_SIGNATURE):
        raise PNGError("Not a PNG file")
    pos, header, idat = len(PNG_SIGNATURE), None, []
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if len(body) != length:
            raise PNGError("Truncated PNG chunk")
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
        pos += 12 + length
    if header is None or not idat:
        raise PNGError("PNG has no IHDR or IDAT")
    width, height, depth, color_type, _, _, interlace = header
    if (depth, color_type, interlace) != (8, 6, 0):
        raise PNGError(f"Unsupported PNG format (bit depth {depth}, color type {color_type}, interlace {interlace})")

    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error as e:
        raise PNGError(f"Corrupt PNG image data: {e}") from None
    stride = width * BYTES_PER_PIXEL
    if len(raw) != height * (stride + 1):
        raise PNGError("PNG image data has the wrong size")

    masks = _lane_masks(stride)
    rows, prev = [], bytes(stride)
    for y in range(height):
        offset = y * (stride + 1)
        kind, row = raw[offset], raw[offset + 1:offset + 1 + stride]
        if kind == 0:
            line = row
        elif kind == 1:
            line = _unfilter_sub(row, masks)
        elif kind == 2:
            line = _unfilter_up(row, prev, masks)
        elif kind == 3:
            line = _unfilter_average(row, prev)
        elif kind == 4:
            line = _unfilter_paeth(row, prev, masks)
        else:
            raise PNGError(f"Unknown PNG filter type {kind}")
        rows.append(line)
        prev = line
    return width, height, rows


def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def encode_rgba(width: int, rows: list[bytes]) -> bytes:
    """Encode RGBA rows as a PNG using the Up filter (repeated rows compress to nothing)."""
    stride = width * BYTES_PER_PIXEL
    high, low, _ = _lane_masks(stride)
    compressor = zlib.compressobj(6)
    parts, prev = [], 0
    for row in rows:
        value = int.from_bytes(row, "little")
        parts.append(compressor.compress(b"\x02" + _sub_bytes(value, prev, high, low).to_bytes(stride, "little")))
        prev = value
    parts.append(compressor.flush())
    header = struct.pack(">IIBBBBB", width, len(rows), 8, 6, 0, 0, 0)
    return PNG_SIGNATURE + _chunk(b"IHDR", header) + _chunk(b"IDAT", b"".join(parts)) + _chunk(b"IEND", b"")


def mercator_y(lat_deg: float) -> float:
    return math.log(math.tan(math.pi / 4 + math.radians(lat_deg) / 2))


def mercator_row_map(src_height: int, south: float, north: float, west: float, east: float,
                     width: int) -> list[int]:
    """Source row index for each output row, top to bottom.

    The output height keeps pixels square in Mercator units at the given width.
    """
    y_top, y_bottom = mercator_y(north), mercator_y(south)
    out_height = round(width * (y_top - y_bottom) / math.radians(east - west))
    mapping = []
    for j in range(out_height):
        y = y_top - (j + 0.5) / out_height * (y_top - y_bottom)
        lat = math.degrees(2 * math.atan(math.exp(y)) - math.pi / 2)
        i = int((north - lat) / (north - south) * src_height)
        mapping.append(min(max(i, 0), src_height - 1))
    return mapping


def reproject_to_mercator(data: bytes, south: float, north: float, west: float, east: float) -> bytes:
    """Plate Carrée RGBA PNG covering the bounds -> Web Mercator PNG with the same bounds."""
    width, height, rows = decode_rgba(data)
    mapping = mercator_row_map(height, south, north, west, east, width)
    return encode_rgba(width, [rows[i] for i in mapping])
