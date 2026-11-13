"""
Sea routing for Outbreak.

A coarse land/water grid (1 degree cells) is built from the world GeoJSON so cargo ships can only
sail through water. A cell counts as land only when *every* sample inside it is land, which keeps
narrow straits (Gibraltar, Bosphorus, Danish straits ...) open. Water cells are labelled into
connected bodies (so a Caspian port can never reach the Atlantic) and routes are found with A*.
"""
import heapq
import json
import math
import os
import urllib.request

RES = 1.0
W, H = 360, 180
SUB = 3                                  # samples per cell, per axis
GEOJSON_URL = "https://raw.githubusercontent.com/johan/world.geo.json/master/countries.geo.json"


def _polygons(geom):
    if geom["type"] == "Polygon":
        return [geom["coordinates"]]
    if geom["type"] == "MultiPolygon":
        return geom["coordinates"]
    return []


def build_land_rows(geo):
    """Returns H strings (south -> north) of '1' (land) / '0' (water)."""
    ny, nx = H * SUB, W * SUB
    samples = [bytearray(nx) for _ in range(ny)]
    for ft in geo["features"]:
        if ft.get("id") == "ATA":
            continue
        for poly in _polygons(ft["geometry"]):
            xs_all = [p[0] for p in poly[0]]
            wrap = min(xs_all) < -150 and max(xs_all) > 150
            cross = [[] for _ in range(ny)]
            for ring in poly:
                pts = [((p[0] + 360 if (wrap and p[0] < 0) else p[0]), p[1]) for p in ring]
                for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
                    if y1 == y2:
                        continue
                    lo, hi = (y1, y2) if y1 < y2 else (y2, y1)
                    k0 = max(0, int(math.ceil((lo + 90) * SUB / RES - 0.5)))
                    k1 = min(ny - 1, int(math.ceil((hi + 90) * SUB / RES - 0.5)) - 1)
                    for k in range(k0, k1 + 1):
                        y = -90 + (k + 0.5) * RES / SUB
                        cross[k].append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
            for k, xs in enumerate(cross):
                if len(xs) < 2:
                    continue
                xs.sort()
                row = samples[k]
                for i in range(0, len(xs) - 1, 2):
                    s0 = int(math.ceil((xs[i] + 180) * SUB / RES - 0.5))
                    s1 = int(math.floor((xs[i + 1] + 180) * SUB / RES - 0.5))
                    for s in range(s0, s1 + 1):
                        row[s % nx] = 1
    rows = []
    for iy in range(H):
        out = []
        for ix in range(W):
            if iy * RES - 90 < -62:                       # Antarctica
                out.append("1")
                continue
            land = all(samples[iy * SUB + a][ix * SUB + b] for a in range(SUB) for b in range(SUB))
            out.append("1" if land else "0")
        rows.append("".join(out))
    return rows


class SeaGrid:
    def __init__(self, rows):
        self.rows = rows
        self.land = [[c == "1" for c in r] for r in rows]
        self.comp = [[-1] * W for _ in range(H)]
        self._label()
        self._cache = {}

    # ---- geometry helpers
    @staticmethod
    def cell(lat, lng):
        return int(((lng + 180) // RES) % W), max(0, min(H - 1, int((lat + 90) // RES)))

    @staticmethod
    def center(c):
        return [round(c[1] * RES - 90 + RES / 2, 2), round(c[0] * RES - 180 + RES / 2, 2)]

    def is_water(self, ix, iy):
        return 0 <= iy < H and not self.land[iy][ix % W]

    def _label(self):
        n = 0
        for iy in range(H):
            for ix in range(W):
                if self.land[iy][ix] or self.comp[iy][ix] != -1:
                    continue
                stack = [(ix, iy)]
                self.comp[iy][ix] = n
                while stack:
                    x, y = stack.pop()
                    for dx in (-1, 0, 1):
                        for dy in (-1, 0, 1):
                            nx_, ny_ = (x + dx) % W, y + dy
                            if (dx or dy) and 0 <= ny_ < H and not self.land[ny_][nx_] and self.comp[ny_][nx_] == -1:
                                self.comp[ny_][nx_] = n
                                stack.append((nx_, ny_))
                n += 1
        self.n_comp = n

    def snap(self, lat, lng, maxr=4):
        """Nearest water cell to a coastal point, or None."""
        cx, cy = self.cell(lat, lng)
        if self.is_water(cx, cy):
            return (cx, cy)
        best, bd = None, 1e9
        for r in range(1, maxr + 1):
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    if max(abs(dx), abs(dy)) != r:
                        continue
                    x, y = (cx + dx) % W, cy + dy
                    if self.is_water(x, y):
                        d = dx * dx + dy * dy
                        if d < bd:
                            best, bd = (x, y), d
            if best:
                return best
        return None

    def component(self, c):
        return self.comp[c[1]][c[0]]

    # ---- routing
    def _los(self, a, b):
        dx = ((b[0] - a[0] + W / 2) % W) - W / 2
        dy = b[1] - a[1]
        n = int(max(abs(dx), abs(dy)) * 3) + 1
        for i in range(n + 1):
            t = i / n
            x = int(round(a[0] + dx * t)) % W
            y = int(round(a[1] + dy * t))
            if not self.is_water(x, y):
                return False
        return True

    def route(self, a, b):
        """Waypoints [[lat, lng], ...] from water cell a to water cell b (lng unwrapped), or None."""
        if a is None or b is None or self.component(a) != self.component(b):
            return None
        key = (a, b)
        if key in self._cache:
            return self._cache[key]
        if a == b:
            self._cache[key] = [self.center(a)]
            return self._cache[key]
        land = self.land
        bx, by = b

        def h(x, y):                                    # octile distance, wrapping around the globe
            dx = abs(x - bx)
            dx = min(dx, W - dx)
            dy = abs(y - by)
            return max(dx, dy) + 0.414 * min(dx, dy)

        openq = [(h(*a), 0.0, a)]
        came, g = {a: None}, {a: 0.0}
        while openq:
            _, gc, cur = heapq.heappop(openq)
            if cur == b:
                break
            if gc > g[cur]:
                continue
            cx, cy = cur
            for dx, dy, cost in _NEIGH:
                nx_, ny_ = (cx + dx) % W, cy + dy
                if ny_ < 0 or ny_ >= H or land[ny_][nx_]:
                    continue
                if dx and dy and land[cy][nx_] and land[ny_][cx]:
                    continue                            # no squeezing through a land corner
                ng = gc + cost
                nb = (nx_, ny_)
                if ng < g.get(nb, 1e9):
                    g[nb] = ng
                    came[nb] = cur
                    heapq.heappush(openq, (ng + h(nx_, ny_), ng, nb))
        if b not in came:
            self._cache[key] = None
            return None
        path, cur = [], b
        while cur is not None:
            path.append(cur)
            cur = came[cur]
        path.reverse()
        # simplify: keep jumping as far ahead as the line of sight allows
        out, i = [path[0]], 0
        while i < len(path) - 1:
            j = i + 1
            while j + 1 < len(path) and self._los(path[i], path[j + 1]):
                j += 1
            out.append(path[j])
            i = j
        pts, prev_lng = [], None
        for c in out:
            lat, lng = self.center(c)
            if prev_lng is not None:
                while lng - prev_lng > 180:
                    lng -= 360
                while lng - prev_lng < -180:
                    lng += 360
            pts.append([lat, lng])
            prev_lng = lng
        self._cache[key] = pts
        return pts


_NEIGH = [(dx, dy, 1.0 if not (dx and dy) else 1.414) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy]


def ensure_sea_grid(data_dir):
    """Loads data/sea_grid.json, building it from the world GeoJSON the first time."""
    path = os.path.join(data_dir, "sea_grid.json")
    try:
        if not os.path.exists(path):
            geo_path = os.path.join(data_dir, "world.geo.json")
            if not os.path.exists(geo_path):
                urllib.request.urlretrieve(GEOJSON_URL, geo_path)
            print("Building data/sea_grid.json (one time, about half a minute) ...")
            with open(geo_path, "r", encoding="utf-8") as f:
                rows = build_land_rows(json.load(f))
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"res": RES, "w": W, "h": H, "rows": rows}, f)
        with open(path, "r", encoding="utf-8") as f:
            return SeaGrid(json.load(f)["rows"])
    except Exception as e:                                   # ships simply stay in port if this fails
        print("Sea grid unavailable:", e)
        return None