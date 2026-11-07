"""Kleiner QR-Code-Erzeuger (Byte-Modus, Fehlerkorrektur M, Version 1–10) für den Kopplungslink – ohne Fremdpaket.

Aufbau nach ISO/IEC 18004: Daten → Blöcke mit Reed-Solomon-Prüfwörtern (GF(256), Polynom 0x11D) → verschränkt im
Zickzack eingetragen → Maske mit der geringsten Strafpunktzahl (Regeln 1–4) → Formatinformation.
Ergebnis als SVG (`svg`) bzw. Matrix (`matrix`, True = dunkel).
"""
from __future__ import annotations

# Version → (Prüfwörter je Block, Anzahl Blöcke) für Fehlerkorrektur M
_ECC_M = {1: (10, 1), 2: (16, 1), 3: (26, 1), 4: (18, 2), 5: (24, 2), 6: (16, 4), 7: (18, 4), 8: (22, 4), 9: (22, 5), 10: (26, 5)}
_FORMAT_M = 0          # Formatbits der Stufe M


def _mul(x: int, y: int) -> int:
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree: int) -> list[int]:
    res = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            res[j] = _mul(res[j], root)
            if j + 1 < degree:
                res[j] ^= res[j + 1]
        root = _mul(root, 0x02)
    return res


def _rs_remainder(data: list[int], div: list[int]) -> list[int]:
    res = [0] * len(div)
    for b in data:
        f = b ^ res.pop(0)
        res.append(0)
        for i, c in enumerate(div):
            res[i] ^= _mul(c, f)
    return res


def _raw_modules(ver: int) -> int:
    n = (16 * ver + 128) * ver + 64
    if ver >= 2:
        k = ver // 7 + 2
        n -= (25 * k - 10) * k - 55
        if ver >= 7:
            n -= 36
    return n


def _data_codewords(ver: int) -> int:
    ecc, blocks = _ECC_M[ver]
    return _raw_modules(ver) // 8 - ecc * blocks


def _align_positions(ver: int, size: int) -> list[int]:
    if ver == 1:
        return []
    k = ver // 7 + 2
    step = (ver * 4 + k * 2 + 1) // (k * 2 - 2) * 2
    return sorted([size - 7 - i * step for i in range(k - 1)] + [6])


class _Grid:
    def __init__(self, ver: int):
        self.ver, self.size = ver, ver * 4 + 17
        self.m = [[False] * self.size for _ in range(self.size)]
        self.fn = [[False] * self.size for _ in range(self.size)]

    def setf(self, x: int, y: int, dark: bool):
        self.m[y][x] = dark
        self.fn[y][x] = True

    def functions(self):
        n = self.size
        for i in range(n):
            self.setf(6, i, i % 2 == 0)
            self.setf(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < n and 0 <= y < n:
                        self.setf(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = _align_positions(self.ver, n)
        last = len(pos) - 1
        for i, ay in enumerate(pos):
            for j, ax in enumerate(pos):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.setf(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self.format_bits(0)
        if self.ver >= 7:
            rem = self.ver
            for _ in range(12):
                rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
            bits = self.ver << 12 | rem
            for i in range(18):
                b = (bits >> i) & 1 == 1
                a, c = n - 11 + i % 3, i // 3
                self.setf(a, c, b)
                self.setf(c, a, b)

    def format_bits(self, mask: int):
        data = _FORMAT_M << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        bit = lambda i: (bits >> i) & 1 == 1
        n = self.size
        for i in range(6):
            self.setf(8, i, bit(i))
        self.setf(8, 7, bit(6))
        self.setf(8, 8, bit(7))
        self.setf(7, 8, bit(8))
        for i in range(9, 15):
            self.setf(14 - i, 8, bit(i))
        for i in range(8):
            self.setf(n - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.setf(8, n - 15 + i, bit(i))
        self.setf(8, n - 8, True)

    def place(self, data: list[int]):
        n, i = self.size, 0
        right = n - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(n):
                for j in range(2):
                    x = right - j
                    up = ((right + 1) & 2) == 0
                    y = n - 1 - vert if up else vert
                    if not self.fn[y][x] and i < len(data) * 8:
                        self.m[y][x] = (data[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def apply_mask(self, mask: int):
        f = (lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0, lambda x, y: (x + y) % 3 == 0,
             lambda x, y: (x // 3 + y // 2) % 2 == 0, lambda x, y: x * y % 2 + x * y % 3 == 0,
             lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0, lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0)[mask]
        for y in range(self.size):
            for x in range(self.size):
                if not self.fn[y][x] and f(x, y):
                    self.m[y][x] = not self.m[y][x]

    def penalty(self) -> int:
        n, m, p = self.size, self.m, 0
        pats = ((True, False, True, True, True, False, True, False, False, False, False),
                (False, False, False, False, True, False, True, True, True, False, True))
        for lines in (m, [list(c) for c in zip(*m)]):
            for row in lines:
                pad = [False] * 4 + row + [False] * 4         # Ruhezone zählt als hell
                p += 40 * sum(tuple(pad[i:i + 11]) in pats for i in range(len(pad) - 10))
                run, prev = 0, None
                for v in row + [None]:
                    if v == prev:
                        run += 1
                    else:
                        if run >= 5:
                            p += run - 2
                        run, prev = 1, v
        for y in range(n - 1):
            for x in range(n - 1):
                if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    p += 3
        dark = sum(sum(r) for r in m)
        p += abs(dark * 20 - n * n * 10) // (n * n) * 10
        return p


def matrix(text: str) -> list[list[bool]]:
    data = text.encode("utf-8")
    ver = next((v for v in _ECC_M if 4 + (8 if v < 10 else 16) + 8 * len(data) <= _data_codewords(v) * 8), None)
    if ver is None:
        raise ValueError("Text zu lang für den QR-Code.")
    bits: list[int] = []
    push = lambda val, n: bits.extend((val >> i) & 1 for i in reversed(range(n)))
    push(0b0100, 4)
    push(len(data), 8 if ver < 10 else 16)
    for b in data:
        push(b, 8)
    cap = _data_codewords(ver) * 8
    push(0, min(4, cap - len(bits)))
    push(0, -len(bits) % 8)
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(words) < cap // 8:
        words.append(pad)
        pad ^= 0xEC ^ 0x11
    # Blöcke + Prüfwörter, verschränkt
    ecc, nblocks = _ECC_M[ver]
    raw = _raw_modules(ver) // 8
    nshort = nblocks - raw % nblocks
    short_len = raw // nblocks
    div = _rs_divisor(ecc)
    blocks, k = [], 0
    for i in range(nblocks):
        dat = words[k:k + short_len - ecc + (0 if i < nshort else 1)]
        k += len(dat)
        e = _rs_remainder(dat, div)
        if i < nshort:
            dat = dat + [0]
        blocks.append(dat + e)
    out = []
    for i in range(len(blocks[0])):
        for j, b in enumerate(blocks):
            if i != short_len - ecc or j >= nshort:
                out.append(b[i])
    g = _Grid(ver)
    g.functions()
    g.place(out)
    best = None
    for mask in range(8):
        g.apply_mask(mask)
        g.format_bits(mask)
        score = g.penalty()
        if best is None or score < best[0]:
            best = (score, mask)
        g.apply_mask(mask)                       # XOR rückgängig
    g.apply_mask(best[1])
    g.format_bits(best[1])
    return g.m


def svg(text: str, border: int = 4) -> str:
    m = matrix(text)
    n = len(m) + 2 * border
    path = "".join(f"M{x + border},{y + border}h1v1h-1z" for y, row in enumerate(m) for x, v in enumerate(row) if v)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" shape-rendering="crispEdges">'
            f'<rect width="{n}" height="{n}" fill="#fff"/><path d="{path}" fill="#000"/></svg>')
