"""
Outbreak - simulation core (daily model).

One turn is one real calendar day: the game starts on the day you start playing.

  * A disease starts in a random country (never yours) and spreads inside regions, between the
    regions of a country, over land borders, by air and on cargo ships that follow real sea lanes.
  * Every country runs its own engine (personality, budget, upgrades, border and lockdown policy,
    ambitions). Infected countries ground flights / close ports; they reopen later by their own rules.
  * After the outbreak is confirmed the world researches a cure. When it reaches 100% the game ends:
    you win if your nation survived, you lose if the disease devastated it.
  * You earn money from your regions, invest it in five upgrade lines, manage lockdowns, evacuations,
    medicine, diplomacy and war.
"""
import datetime
import json
import math
import os
import random
import urllib.request

from . import events
from .seagrid import ensure_sea_grid

GEOJSON_URL = "https://raw.githubusercontent.com/johan/world.geo.json/master/countries.geo.json"
DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# ============================== WORLD GENERATION ==============================
def _polygons(geom):
    if geom["type"] == "Polygon":
        return [geom["coordinates"]]
    if geom["type"] == "MultiPolygon":
        return geom["coordinates"]
    return []


def _ring_area_km2(ring):
    s = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        s += x1 * y2 - x2 * y1
    lat = sum(p[1] for p in ring) / len(ring)
    return abs(s) / 2 * 111.32 * 111.32 * math.cos(math.radians(lat))


def _in_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def _in_poly(x, y, poly):
    if not _in_ring(x, y, poly[0]):
        return False
    return not any(_in_ring(x, y, hole) for hole in poly[1:])


def _kmeans(points, k, rng, iters=14):
    centers = [rng.choice(points)]
    while len(centers) < k:
        d = [min((px - cx) ** 2 + (py - cy) ** 2 for cx, cy in centers) for px, py in points]
        tot = sum(d)
        if tot == 0:
            centers.append(rng.choice(points))
            continue
        r, acc = rng.uniform(0, tot), 0.0
        for p, dd in zip(points, d):
            acc += dd
            if acc >= r:
                centers.append(p)
                break
        else:
            centers.append(points[-1])
    clusters = []
    for _ in range(iters):
        clusters = [[] for _ in centers]
        for p in points:
            i = min(range(k), key=lambda i: (p[0] - centers[i][0]) ** 2 + (p[1] - centers[i][1]) ** 2)
            clusters[i].append(p)
        centers = [
            (sum(q[0] for q in cl) / len(cl), sum(q[1] for q in cl) / len(cl)) if cl else centers[i]
            for i, cl in enumerate(clusters)
        ]
    return centers, clusters


_SECTORS = ["East", "Northeast", "North", "Northwest", "West", "Southwest", "South", "Southeast"]
_ROMAN = ["", " II", " III", " IV", " V"]


def _region_names(centers):
    """Name regions by compass direction relative to the middle of the country."""
    n = len(centers)
    mx = sum(c[0] for c in centers) / n
    my = sum(c[1] for c in centers) / n
    spread = max(math.hypot(c[0] - mx, c[1] - my) for c in centers) or 1.0
    names, used = [], {}
    for cx, cy in centers:
        if n > 2 and math.hypot(cx - mx, cy - my) < 0.28 * spread:
            nm = "Central"
        else:
            ang = math.degrees(math.atan2(cy - my, cx - mx)) % 360
            nm = _SECTORS[int((ang + 22.5) // 45) % 8]
        used[nm] = used.get(nm, 0) + 1
        names.append(nm + _ROMAN[used[nm] - 1] if used[nm] > 1 else nm)
    return names


def generate_world(data_dir, geojson_path):
    """Split every country into 2-5 macro-regions. Also computes land borders and coast points."""
    with open(geojson_path, "r", encoding="utf-8") as f:
        geo = json.load(f)
    with open(os.path.join(data_dir, "countries.json"), "r", encoding="utf-8") as f:
        countries = json.load(f)
    by_iso3 = {c["iso3"]: c for c in countries}
    feats = {ft["id"]: ft for ft in geo["features"] if ft.get("id") in by_iso3}

    # ---- vertex index (border + coast detection) ----
    CELL = 50.0  # 0.02 degrees
    owners = {}
    vkeys = {}
    for iso3, ft in feats.items():
        keys = []
        for poly in _polygons(ft["geometry"]):
            for ring in poly:
                for p in ring:
                    k = (int(round(p[0] * CELL)), int(round(p[1] * CELL)))
                    owners.setdefault(k, set()).add(iso3)
                    keys.append(k)
        vkeys[iso3] = keys

    def near(k):
        s = set()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                s |= owners.get((k[0] + dx, k[1] + dy), set())
        return s

    shared = {}
    for iso3, keys in vkeys.items():
        for k in set(keys):
            for o in near(k) - {iso3}:
                shared.setdefault(iso3, {}).setdefault(o, 0)
                shared[iso3][o] += 1
    borders = {}
    for a, row in shared.items():
        for b, n in row.items():
            if n >= 3 and shared.get(b, {}).get(a, 0) >= 3:
                borders.setdefault(by_iso3[a]["id"], []).append(by_iso3[b]["id"])

    regions, coast = [], {}
    for c in countries:
        iso3, iso2 = c["iso3"], c["id"]
        ft = feats.get(iso3)
        if not ft:
            continue
        rng = random.Random("regions-" + iso3)
        polys = _polygons(ft["geometry"])
        areas = [max(_ring_area_km2(p[0]), 1.0) for p in polys]
        total = sum(areas)
        k = 2 if total < 100_000 else 3 if total < 500_000 else 4 if total < 2_000_000 else 5

        # coast points (vertices that touch no other country)
        if c.get("has_port"):
            cands = []
            for poly, ar in sorted(zip(polys, areas), key=lambda t: -t[1])[:3]:
                for p in poly[0]:
                    kk = (int(round(p[0] * CELL)), int(round(p[1] * CELL)))
                    if not (near(kk) - {iso3}):
                        cands.append([round(p[1], 2), round(p[0], 2)])
            step = max(1, len(cands) // 10)
            coast[iso2] = cands[::step][:10]

        # sample interior points (unwrap the antimeridian for countries such as Russia / USA)
        lons = [p[0] for poly in polys for p in poly[0]]
        wrap = min(lons) < -150 and max(lons) > 150
        fix = (lambda lon: lon + 360 if lon < 0 else lon) if wrap else (lambda lon: lon)
        lat0 = sum(p[1] for poly in polys for p in poly[0]) / len(lons)
        kx = math.cos(math.radians(lat0))
        samples = []
        weights = [a ** 0.85 for a in areas]
        for _ in range(170):
            i = rng.choices(range(len(polys)), weights=weights)[0]
            poly = polys[i]
            xs = [p[0] for p in poly[0]]
            ys = [p[1] for p in poly[0]]
            pt = None
            for _t in range(70):
                x, y = rng.uniform(min(xs), max(xs)), rng.uniform(min(ys), max(ys))
                if _in_poly(x, y, poly):
                    pt = (x, y)
                    break
            if pt is None:
                v = rng.choice(poly[0])
                pt = (v[0], v[1])
            samples.append(pt)
        pts = [(fix(x) * kx, y) for x, y in samples]
        k = max(1, min(k, len(set(pts))))
        centers, clusters = _kmeans(pts, k, rng)

        # region anchor = the sampled point (inside the country) closest to each cluster centre
        anchors, taken = [], set()
        for (cx, cy), cl in zip(centers, clusters):
            pool = cl or pts
            best = min((p for p in pool if p not in taken), key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2,
                       default=pool[0])
            taken.add(best)
            anchors.append(best)
        names = _region_names(anchors)
        w = [rng.uniform(0.55, 1.5) for _ in anchors]
        sw = sum(w)
        for (ax, ay), nm, wi in zip(anchors, names, w):
            lon = ax / kx
            lon = lon - 360 if lon > 180 else lon
            regions.append({
                "id": "%s_%s" % (iso2, nm.upper().replace(" ", "_")),
                "country_id": iso2,
                "name": nm,
                "lat": round(ay, 3),
                "lng": round(lon, 3),
                "population": int(round(c["population"] * wi / sw, -3)),
            })
    return {"regions": regions, "borders": borders, "coast": coast}


def ensure_world(data_dir=None):
    """Return (countries, world) - generating data/regions.json from the GeoJSON if it is missing."""
    data_dir = data_dir or DEFAULT_DATA_DIR
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, "countries.json"), "r", encoding="utf-8") as f:
        countries = json.load(f)
    regions_path = os.path.join(data_dir, "regions.json")
    if not os.path.exists(regions_path):
        geo_path = os.path.join(data_dir, "world.geo.json")
        if not os.path.exists(geo_path):
            print("Downloading world GeoJSON ...")
            urllib.request.urlretrieve(GEOJSON_URL, geo_path)
        print("Generating data/regions.json ...")
        world = generate_world(data_dir, geo_path)
        with open(regions_path, "w", encoding="utf-8") as f:
            f.write('{"regions": [\n')
            f.write(",\n".join("  " + json.dumps(r, ensure_ascii=False) for r in world["regions"]))
            f.write('\n],\n"borders": %s,\n"coast": %s}\n' % (json.dumps(world["borders"]), json.dumps(world["coast"])))
    with open(regions_path, "r", encoding="utf-8") as f:
        world = json.load(f)
    return countries, world


def list_countries(data_dir=None):
    data_dir = data_dir or DEFAULT_DATA_DIR
    with open(os.path.join(data_dir, "countries.json"), "r", encoding="utf-8") as f:
        return [{"id": c["id"], "iso3": c["iso3"], "name": c["name"], "population": c["population"]}
                for c in json.load(f)]


# ============================== TUNING (1 turn = 1 day) ==============================
START_BUDGET = 150
INCOME_SCALE = 0.25          # $ per day = INCOME_SCALE * (3 + sum of region values)
LOCAL_R = 0.09               # logistic growth per day inside a region
SEED_INFECTION = 0.003       # a fresh outbreak starts with 0.3% of the region infected
PATIENT_ZERO_START = 0.004
DOMESTIC_BASE = 0.025        # per infected region and day: spread to another region of the same country
AIR_BASE = 0.050             # per infected country with open airports, per day
SEA_BASE = 0.035
LAND_BASE = 0.028            # per infected country and neighbour, per day
DETECT_DAY = 60              # the outbreak is always confirmed by this day
CURE_BASE = 0.00060          # global cure progress per day once research has started
CURE_COUNTRY = 0.00000120    # extra progress from every country (wealth, infection, research)
SEV_RAMP_DAYS = 480          # the pathogen reaches full lethality after this many days
LOCK_GROWTH = 0.55           # local growth multiplier under lockdown
CORDON_GROWTH = 0.35
VICTORY_MAX_DEAD = 0.25
RIOT_THRESHOLD = 0.12
STAB_CLOSED = 0.0015         # stability lost per day with closed borders
STAB_RECOVER = 0.0020
MED_CAP = 800

DISEASES = {
    #            local  air   sea   land  lethality/day  cure difficulty
    "bacteria": dict(local=1.00, air=0.80, sea=1.25, land=1.00, lethality=0.0008, cure=1.00),
    "virus":    dict(local=1.10, air=1.30, sea=0.90, land=1.10, lethality=0.0012, cure=1.25),
    "zombie":   dict(local=1.20, air=0.40, sea=0.70, land=1.60, lethality=0.0025, cure=0.90),
}
DISEASE_NAMES = {
    "bacteria": ["Black Bloom", "Vibrio Nova", "Gram-X"],
    "virus": ["Crimson Fever", "H9-Reaper", "Nova-19"],
    "zombie": ["Pale Hunger", "Z-Day", "Dead Rising"],
}

# ---- the pathogen: phases and mutations ----
PHASES = {1: "Outbreak", 2: "Epidemic", 3: "Pandemic", 4: "Collapse"}
MUTATIONS = {
    "airborne": ("Airborne", "It now travels on every flight: air spread +50%. Think about grounding your planes.", {"air": 1.5}),
    "waterborne": ("Waterborne", "It survives in water and ships: sea spread +50%. Think about your ports.", {"sea": 1.5}),
    "vector": ("Vector-borne", "Rats and insects carry it overland: land and domestic spread +40%.", {"land": 1.4, "dom": 1.3}),
    "hemorrhagic": ("Hemorrhagic", "It kills faster: lethality +40%.", {"leth": 1.4}),
    "resistant": ("Drug-resistant", "Your medicine protects 35% less.", {"res": 0.65}),
    "hardy": ("Quarantine-proof", "Lockdowns and cordons leak far more.", {"leak": 0.25}),
    "silent": ("Silent carriers", "People spread it without symptoms: border screening works 35% worse.", {"border": 0.65}),
    "stealth": ("Stealth strain", "It spreads 25% faster but kills 25% less.", {"grow": 1.25, "leth": 0.75}),
}

# ---- upgrade tracks: 5 tiers each, bought in order ----
TIER_COST = [60, 120, 220, 380, 600]
ECON_MULT = [1.0, 1.15, 1.30, 1.50, 1.75, 2.0]
MIL_POWER = [1.0, 1.25, 1.55, 1.9, 2.3, 2.8]
RES = [0.0, 0.05, 0.12, 0.25, 0.40, 0.60]            # medicine: how hard it is to infect your people
HOSP_LETH = [1.0, 0.90, 0.82, 0.72, 0.62, 0.50]      # medicine: lethality multiplier
RESEARCH_CURE = [0.0, 0.06, 0.13, 0.21, 0.30, 0.40]  # research: faster global cure
BORDER_AIRSEA = [1.0, 0.80, 0.60, 0.45, 0.30, 0.20]  # border control: share of importations that get through
BORDER_LAND = [1.0, 0.90, 0.70, 0.50, 0.35, 0.25]
MED_PRODUCTION = [0, 0, 0, 6, 14, 28]                # doses per day

TRACKS = {
    "economy": ("Economy", "Income and trade.",
                [("Tax reform", "Income +15%."),
                 ("Trade hubs", "Income +30%. Unlocks trade agreements."),
                 ("Industrial zones", "Income +50%."),
                 ("Logistics network", "Income +75%."),
                 ("Mega-economy", "Income +100%.")]),
    "military": ("Military", "Army strength: invasions, defence, keeping conquered land quiet.",
                 [("Barracks", "Army strength x1.25."),
                  ("Munition factories", "Army strength x1.55."),
                  ("Armoured divisions", "Army strength x1.9."),
                  ("Air force", "Army strength x2.3."),
                  ("Rapid deployment", "Army strength x2.8.")]),
    "medicine": ("Medicine", "Protects your own people. From tier 3 you produce doses you can keep or sell.",
                 [("Clinics", "5% harder to infect your people. Deaths -10%."),
                  ("Testing and tracing", "12% harder to infect. Deaths -18%."),
                  ("Pharma factories", "25% harder to infect. Produces 6 doses/day: keep them or sell them."),
                  ("Vaccine lab", "40% harder to infect. 14 doses/day."),
                  ("Mass immunisation", "60% harder to infect. 28 doses/day. Deaths -50%.")]),
    "research": ("Research", "The global cure. Speeds it up for everyone.",
                 [("Research grants", "Global cure +6%."),
                  ("University labs", "Global cure +13%."),
                  ("Genome sequencing", "Global cure +21%."),
                  ("Global lab network", "Global cure +30%."),
                  ("Cure breakthrough", "Global cure +40%.")]),
    "border": ("Border control", "Screening that works even while your borders are open.",
               [("Health screening", "20% of infected arrivals are stopped (air, sea)."),
                ("Port inspections", "40% stopped. Land 30%."),
                ("Border fences", "55% stopped. Land 50%."),
                ("Quarantine zones", "70% stopped. Land 65%."),
                ("Smart borders", "80% stopped. Land 75%.")]),
}

PERSONALITY = {
    "balanced":     dict(label="Balanced",     close=0.08, paranoid=False, reopen=45,  lock=0.25, aggr=0.0,
                         spend={"economy": 2, "medicine": 2, "military": 2, "research": 1, "border": 2}),
    "isolationist": dict(label="Isolationist", close=0.0,  paranoid=True,  reopen=150, lock=0.12, aggr=0.0,
                         spend={"border": 4, "medicine": 2, "economy": 1, "military": 1, "research": 1}),
    "militarist":   dict(label="Militarist",   close=0.20, paranoid=False, reopen=35,  lock=0.30, aggr=1.0,
                         spend={"military": 5, "economy": 2, "border": 1, "medicine": 1, "research": 0.01}),
    "scientist":    dict(label="Scientist",    close=0.12, paranoid=False, reopen=60,  lock=0.20, aggr=0.0,
                         spend={"research": 3, "medicine": 3, "economy": 1, "border": 1, "military": 0.3}),
    "trader":       dict(label="Trader",       close=0.40, paranoid=False, reopen=25,  lock=0.45, aggr=0.2,
                         spend={"economy": 4, "border": 1, "medicine": 1, "military": 1, "research": 1}),
}


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


# ============================== SIMULATION ==============================
class Simulation:
    def __init__(self, player_country="RO", disease="random", data_dir=None, seed=None, start_date=None):
        self.rng = random.Random(seed)
        self.data_dir = data_dir or DEFAULT_DATA_DIR
        countries, world = ensure_world(self.data_dir)
        self.sea = ensure_sea_grid(self.data_dir)
        self.start_date = start_date or datetime.date.today()     # the game starts on the day you start playing

        self.countries = {}
        for i, c in enumerate(countries):
            c = dict(c)
            c.update(air_open=True, sea_open=True, land_open=True, closed_turn=-999, idx=i, merged=False, budget=0.0,
                     tech={t: 0 for t in TRACKS}, borders=world["borders"].get(c["id"], []),
                     next_attack=0, aid_until=0, personality="balanced", ports=[], port_pos=[], sea_comps=set())
            if self.sea and c["has_port"]:
                seen = set()
                for lat, lng in world["coast"].get(c["id"], []):
                    cell = self.sea.snap(lat, lng)
                    if cell and cell not in seen:
                        seen.add(cell)
                        c["ports"].append(cell)
                        c["port_pos"].append(self.sea.center(cell))
                        c["sea_comps"].add(self.sea.component(cell))
            self.countries[c["id"]] = c
        if player_country not in self.countries:
            raise ValueError("Unknown country: %s" % player_country)

        self.regions, self.by_country = {}, {}
        for r in world["regions"]:
            r = dict(r)
            r.update(owner=r["country_id"], infection=0.0, dead=0.0, had_deaths=False, lockdown=False, cordon=False,
                     unrest=0.0, purged=False)
            self.regions[r["id"]] = r
            self.by_country.setdefault(r["country_id"], []).append(r["id"])

        self.player = player_country
        self.disease = disease if disease in DISEASES else self.rng.choice(list(DISEASES))
        self.disease_name = self.rng.choice(DISEASE_NAMES[self.disease])
        self.D = DISEASES[self.disease]

        # ---- player state
        self.turn = 0
        self.budget = float(START_BUDGET)
        self.income = 0.0
        self.income_detail = {}
        self.tech = {t: 0 for t in TRACKS}
        self.stability = self.countries[player_country]["stability"]
        self.conquests = 0
        self.med_stock = 0.0
        self.med_policy = "balanced"
        self.med_exported = 0
        self.med_sales_today = 0.0
        self.union = set()
        self.allied, self.trade, self.war = set(), set(), set()
        self.war_since, self.truce, self.cooldown, self.ai_rel = {}, {}, {}, {}
        self.rel, self.rel_base = {}, {}
        my_borders = set(self.countries[player_country]["borders"])
        for cid, c in self.countries.items():
            if cid == player_country:
                continue
            base = self.rng.uniform(-8, 28) - (4 if cid in my_borders else 0)
            self.rel_base[cid] = round(base)
            self.rel[cid] = float(round(base))
            weights = {"balanced": 3, "isolationist": 1.2,
                       "militarist": 1.0 + (1.2 if c["stability"] < 0.4 else 0),
                       "scientist": 1.0 + (1.0 if c["stability"] > 0.7 else 0),
                       "trader": 1.0 + (0.8 if c["population"] > 20e6 else 0)}
            c["personality"] = self.rng.choices(list(weights), weights=list(weights.values()))[0]

        # ---- world state
        self.cure_started = False
        self.cure_progress = 0.0
        self.cure_owner = None
        self.log, self.news_seq, self._new_news = [], 0, []
        self.traffic = []
        self._stop = False
        self._adapt = 1.0
        self.game_over = False
        self.result, self.result_text = None, ""
        self._milestones = set()
        self.mods, self.event_cd = {}, {}
        self.mm = {"grow": 1.0, "leth": 1.0, "air": 1.0, "sea": 1.0, "land": 1.0, "dom": 1.0, "res": 1.0, "border": 1.0, "leak": 0.0}
        self.mutations, self.phase = [], 1
        self.next_mutation = self.rng.randint(70, 100)
        self.next_event = self.rng.randint(12, 20)
        self._pending, self._queue, self._uid, self._cards_done = None, [], 0, set()
        self._refresh_cache()
        self._spawn_patient_zero()
        self._refresh_cache()
        pz = next(r for r in self.regions.values() if r["infection"] > 0)
        self._post(events.card("intro", "Day 0", "A cluster of unexplained deaths in %s. The local doctors have no name for it. The world does not know yet. You do. Fourteen thousand kilometres of border, and every one of them is a door." % self._rname(pz), "outbreak"))
        self.income = self._economy(apply=False)

    # ------------------------------------------------------------ small helpers
    def date_of(self, turn=None):
        return self.start_date + datetime.timedelta(days=self.turn if turn is None else turn)

    def _news(self, msg, imp=False, kind="world"):
        self.news_seq += 1
        e = {"id": self.news_seq, "date": self.date_of().isoformat(), "text": msg, "imp": bool(imp), "kind": kind}
        self.log.insert(0, e)
        del self.log[300:]
        self._new_news.append(e)
        if imp:
            self._stop = True

    def _cname(self, cid):
        return self.countries[cid]["name"]

    def _rname(self, r):
        return "%s, %s" % (r["name"], self._cname(r["country_id"]))

    def _my_regions(self):
        return [r for r in self.regions.values() if r["owner"] == self.player]

    def _tech_of(self, owner):
        return self.tech if owner == self.player else self.countries[owner]["tech"]

    def _refresh_cache(self):
        by_owner, owned, cpop = {}, {}, {}
        for r in self.regions.values():
            o, pop, dead = r["owner"], r["population"], r["dead"]
            alive = pop * (1 - dead)
            lst = by_owner.get(o)
            if lst is None:
                by_owner[o] = [r]
                owned[o] = alive
            else:
                lst.append(r)
                owned[o] += alive
            cp = cpop.get(r["country_id"])
            if cp is None:
                cpop[r["country_id"]] = [pop, alive * r["infection"], pop * dead]
            else:
                cp[0] += pop
                cp[1] += alive * r["infection"]
                cp[2] += pop * dead
        self._by_owner, self._owned_pop = by_owner, owned
        self._cinf = {cid: (v[1] / v[0] if v[0] else 0.0) for cid, v in cpop.items()}
        total = sum(v[0] for v in cpop.values()) or 1.0
        self._world_share = sum(v[1] for v in cpop.values()) / total
        self._world_dead = sum(v[2] for v in cpop.values())
        self._world_dead_share = self._world_dead / total
        self._fallen = [cid for cid, v in cpop.items() if v[0] and v[2] / v[0] >= 0.9]
        self._my_cids = {r["country_id"] for r in by_owner.get(self.player, [])} | {self.player}

    def _resistance(self, owner):
        if owner == self.player:
            bonus = 0.0
            if self.tech["medicine"] >= 3:
                if self.med_policy == "keep" and self.med_stock >= 100:
                    bonus = 0.10
                elif self.med_policy == "balanced" and self.med_stock >= 60:
                    bonus = 0.06
            return min(0.85, (RES[self.tech["medicine"]] + bonus) * self.mm["res"])
        c = self.countries[owner]
        return min(0.85, (RES[c["tech"]["medicine"]] + (0.15 if self.turn < c["aid_until"] else 0.0)) * self.mm["res"])

    def _blocked(self, r, mode):
        """Closed player borders stop anything leaving or entering a region the player owns."""
        return r["owner"] == self.player and not self.countries[self.player][mode + "_open"]

    def _emits(self, r):
        """A locked-down region still leaks now and then; a lethal cordon leaks far less."""
        k = min(1.0, self.turn / 600)                 # the pathogen adapts and slips through more and more
        if r["cordon"]:
            return self.rng.random() < 0.05 + 0.15 * k + self.mm["leak"] * 0.5
        if r["lockdown"]:
            return self.rng.random() < 0.15 + 0.45 * k + self.mm["leak"]
        return True

    def _spawn_patient_zero(self):
        options = [c for c in self.countries if c != self.player and self.by_country.get(c)]
        cid = self.rng.choice(options)
        r = self.regions[self.rng.choice(self.by_country[cid])]
        r["infection"] = PATIENT_ZERO_START
        self._news("BREAKING: Patient zero detected in %s! (%s)" % (self._rname(r), self.disease_name))

    def _region_value(self, r):
        pop_m = r["population"] / 1e6
        health = max(0.0, 1 - 0.7 * r["infection"]) * (1 - r["dead"])
        return (4 + 1.2 * math.sqrt(pop_m)) * health

    # ------------------------------------------------------------ military
    def power(self, owner):
        pop_m = self._owned_pop.get(owner, 0.0) / 1e6
        if owner == self.player:
            st, tier = self.stability, self.tech["military"]
        else:
            c = self.countries[owner]
            st, tier = c["stability"] * (1 - 0.5 * self._cinf.get(owner, 0.0)), c["tech"]["military"]
        return 10 * math.sqrt(max(pop_m, 0.01)) * (0.6 + 0.8 * st) * MIL_POWER[tier] * (self._mod("army_weak", 0.8) if owner == self.player else 1.0)

    def invade_info(self, r):
        defender = r["owner"]
        cost = int((30 + 5 * math.sqrt(r["population"] / 1e6)) * (1 + 0.10 * self.conquests))
        share = r["population"] / max(1.0, self._owned_pop.get(defender, 1.0))
        adj = any(n in self.countries[defender]["borders"] for n in self._my_cids) if defender in self.countries else False
        proj = 0.5 + 0.1 * self.tech["military"] + (0.2 if adj else 0.0)
        ratio = self.power(self.player) * proj / max(1e-6, self.power(defender) * (0.35 + 0.65 * share))
        return cost, _clamp(ratio / (ratio + 1), 0.07, 0.90), adj

    # ------------------------------------------------------------ economy
    def _economy(self, apply=True):
        mine = self._my_regions()
        base = 3 + sum(self._region_value(r) * (0.5 if r["lockdown"] else 1.0) for r in mine)
        gross = INCOME_SCALE * base * ECON_MULT[self.tech["economy"]] * (0.6 + 0.8 * self.stability)
        closed = self.closed_modes
        gross *= 1 - 0.2 * ("air" in closed) - 0.15 * ("sea" in closed) - 0.15 * ("land" in closed)
        gross *= self._mod("ration", 0.9) * self._mod("junta", 0.9)
        trade = sum(0.35 * math.sqrt(self.countries[c]["population"] / 1e6) * INCOME_SCALE
                    for c in self.trade if c not in self.war)
        sanctions = min(0.30, 0.03 * len(self.war))
        upkeep = INCOME_SCALE * (1.2 * sum(1 for r in mine if r["lockdown"]) + 2.0 * sum(1 for r in mine if r["cordon"])
                                 + 1.4 * sum(1 for r in mine if r["country_id"] not in self.union and r["country_id"] != self.player))
        net = gross * (1 - sanctions) + trade + self.med_sales_today - upkeep
        self.income_detail = {"gross": round(gross, 1), "trade": round(trade, 1), "medicine": round(self.med_sales_today, 1),
                              "upkeep": round(-upkeep, 1), "sanctions": round(-gross * sanctions, 1)}
        if apply:
            self.budget = max(0.0, self.budget + net)
        return round(net, 1)

    def _player_stability(self):
        base = self.countries[self.player]["stability"]
        mine = self._my_regions()
        locks = sum(1 for r in mine if r["lockdown"])
        cords = sum(1 for r in mine if r["cordon"])
        n_closed = len(self.closed_modes)
        d = -STAB_CLOSED * n_closed / 3 if n_closed else STAB_RECOVER
        d -= 0.0004 * locks + 0.0015 * cords + 0.0004 * len(self.war) + 0.006 * self._cinf.get(self.player, 0.0)
        st = self.stability + d
        st = min(st, max(base, self.stability))
        self.stability = _clamp(st, 0.0, 1.0)
        if self.stability <= RIOT_THRESHOLD and (n_closed or locks or cords):
            self._set_channels(["air", "sea", "land"], False)
            for r in mine:
                r["lockdown"] = r["cordon"] = False
            self.stability = 0.2
            self._news("RIOTS: unrest forces %s to lift its borders, lockdowns and cordons!" % self._cname(self.player), imp=True, kind="local")

    # ------------------------------------------------------------ epidemic
    def _grow(self):
        sev = min(1.0, 0.15 + 0.85 * self.turn / SEV_RAMP_DAYS)
        D = self.D
        for r in self.regions.values():
            self._occupation(r)
            inf = r["infection"]
            if inf <= 0 or r["purged"]:
                continue
            owner = r["owner"]
            res = self._resistance(owner)
            g = LOCAL_R * D["local"] * self.mm["grow"] * (1 - 0.6 * res)
            if r["lockdown"]:
                g *= LOCK_GROWTH
            if r["cordon"]:
                g *= CORDON_GROWTH
            inf = min(1.0, inf + g * inf * (1 - inf) + (0.0005 if inf < 0.2 else 0.0))
            r["infection"] = inf
            leth = D["lethality"] * sev * self.mm["leth"] * HOSP_LETH[self._tech_of(owner)["medicine"]]
            if owner == self.player:
                leth *= self._mod("hosp_a", 0.7) * self._mod("hosp_b", 0.85) * self._mod("volunteers", 0.9)
            r["dead"] = min(1.0, r["dead"] + leth * inf * (1 - r["dead"]))
            if r["cordon"]:
                r["dead"] = min(1.0, r["dead"] + 0.004 * (1 - r["dead"]))
            if not r["had_deaths"] and r["dead"] > 0.001:
                r["had_deaths"] = True
                self._news("First deaths reported in %s." % self._rname(r), imp=(owner == self.player), kind="local")

    def _occupation(self, r):
        if r["owner"] != self.player or r["country_id"] == self.player or r["country_id"] in self.union:
            return
        r["unrest"] += 0.0035 * (1 - 0.12 * self.tech["military"]) * (1.6 - self.stability)
        if r["unrest"] >= 1.0:
            home = r["country_id"]
            r["owner"], r["unrest"], r["lockdown"], r["cordon"] = home, 0.0, False, False
            self.conquests = max(0, self.conquests - 1)
            self._news("UPRISING: %s throws off your occupation and returns to %s!" % (self._rname(r), self._cname(home)),
                       imp=True, kind="dip")

    def _domestic_spread(self):
        for cid, ids in self.by_country.items():
            regs = [self.regions[i] for i in ids]
            src = [r for r in regs if r["infection"] >= 0.1 and self._emits(r)]
            clean = [r for r in regs if r["infection"] < 0.001 and not r["purged"]]
            if not src or not clean:
                continue
            if self.rng.random() < min(0.9, DOMESTIC_BASE * self._adapt * self.mm["dom"] * len(src) * (0.5 + max(r["infection"] for r in src))):
                t = self.rng.choice(clean)
                t["infection"] = SEED_INFECTION

    def _gets_through(self, tgt, mode):
        tech = self._tech_of(tgt["owner"])
        mult = (BORDER_LAND if mode == "land" else BORDER_AIRSEA)[tech["border"]]
        mult = 1 - (1 - mult) * self.mm["border"]
        if tgt["owner"] == self.player:
            mult *= self._mod("screening", 0.7)
        return self.rng.random() < mult * (1 - 0.7 * self._resistance(tgt["owner"]))

    def _traffic(self, kind, src, dst, blocked=False, path=None):
        t = {"kind": kind, "from": [src["lat"], src["lng"]], "to": [dst["lat"], dst["lng"]],
             "blocked": blocked, "src": src["country_id"], "dst": dst["country_id"]}
        if path:
            t["path"] = path
        self.traffic.append(t)

    def _arrival(self, tgt, mode, cname_src, src, path=None):
        """An infected traveller reaches tgt. Returns True if the infection took hold."""
        mine = tgt["owner"] == self.player
        if self._blocked(tgt, mode):
            self._traffic(mode, src, tgt, blocked=True, path=path)
            if self.rng.random() < 0.5:
                self._news("%s's closed borders stopped infected %s from %s." % (
                    self._cname(self.player), {"air": "flights", "sea": "ships", "land": "travellers"}[mode], cname_src), kind="local")
            return False
        if not self._gets_through(tgt, mode):
            self._traffic(mode, src, tgt, blocked=True, path=path)
            if mine and self.rng.random() < 0.6:
                self._news("Screening stopped an infected arrival from %s at %s." % (cname_src, self._rname(tgt)), kind="local")
            return False
        tgt["infection"] = max(tgt["infection"], SEED_INFECTION)
        self._traffic(mode, src, tgt, path=path)
        return True

    def _spread_air_sea(self, mode):
        base = AIR_BASE if mode == "air" else SEA_BASE
        need = "has_airport" if mode == "air" else "has_port"
        for cid, c in self.countries.items():
            if not (c[need] and c[mode + "_open"]):
                continue
            s = self._cinf.get(cid, 0.0)
            if s < 0.003 or self.rng.random() >= base * self._adapt * self.D[mode] * self.mm[mode] * (0.15 + min(1.0, s * 1.5)):
                continue
            sources = [r for r in (self.regions[i] for i in self.by_country.get(cid, []))
                       if r["infection"] >= 0.01 and self._emits(r) and not self._blocked(r, mode)]
            if not sources:
                continue
            src = max(sources, key=lambda r: r["infection"])
            path = None
            if mode == "air":
                cands = [r for r in self.regions.values() if r["country_id"] != cid and r["infection"] < 0.001 and not r["purged"]
                         and self.countries[r["country_id"]]["has_airport"] and self.countries[r["country_id"]]["air_open"]]
            else:
                if not self.sea or not c["ports"]:
                    continue
                sp = min(range(len(c["ports"])), key=lambda i: (c["port_pos"][i][0] - src["lat"]) ** 2 + (c["port_pos"][i][1] - src["lng"]) ** 2)
                comp = self.sea.component(c["ports"][sp])
                cands = [r for r in self.regions.values() if r["country_id"] != cid and r["infection"] < 0.001 and not r["purged"]
                         and self.countries[r["country_id"]]["sea_open"] and comp in self.countries[r["country_id"]]["sea_comps"]]
            if not cands:
                continue
            tgt = self.rng.choices(cands, weights=[math.sqrt(r["population"]) + 1.0 for r in cands])[0]
            tc = self.countries[tgt["country_id"]]
            if mode == "sea":
                tp = min((i for i, cell in enumerate(tc["ports"]) if self.sea.component(cell) == comp),
                         key=lambda i: (tc["port_pos"][i][0] - tgt["lat"]) ** 2 + (tc["port_pos"][i][1] - tgt["lng"]) ** 2)
                path = self.sea.route(c["ports"][sp], tc["ports"][tp])
                if not path:
                    continue
            if not self._arrival(tgt, mode, c["name"], src, path=path):
                continue
            theirs = tgt["country_id"] != self.player and tgt["country_id"] not in self.union
            if theirs:                                           # the new victim shuts its airports / ports at once
                tc[mode + "_open"] = False
                tc["closed_turn"] = self.turn
            mine = tgt["owner"] == self.player
            if mode == "air":
                self._news("ALERT: %s from %s flew to %s!%s" % (self.disease.capitalize(), c["name"], self._rname(tgt),
                                                                " Airport closed." if theirs else ""), imp=mine, kind="air")
            else:
                self._news("ALERT: Cargo ship from %s docked in %s!%s" % (c["name"], self._rname(tgt),
                                                                           " Port closed." if theirs else ""), imp=mine, kind="sea")

    def _spread_land(self):
        for cid, c in self.countries.items():
            s = self._cinf.get(cid, 0.0)
            if not c["land_open"] or s < 0.003:
                continue
            sources = [r for r in (self.regions[i] for i in self.by_country.get(cid, []))
                       if r["infection"] >= 0.01 and self._emits(r) and not self._blocked(r, "land")]
            if not sources:
                continue
            p = LAND_BASE * self._adapt * self.D["land"] * self.mm["land"] * (0.1 + min(1.0, s * 1.5))
            for nid in c["borders"]:
                nb = self.countries.get(nid)
                if not nb or not nb["land_open"] or self.rng.random() >= p:
                    continue
                clean = [self.regions[i] for i in self.by_country.get(nid, []) if self.regions[i]["infection"] < 0.001
                         and not self.regions[i]["purged"]]
                if not clean:
                    continue
                tgt = self.rng.choice(clean)
                src = max(sources, key=lambda r: r["infection"])
                if self._arrival(tgt, "land", c["name"], src):
                    self._news("The %s crossed the border from %s into %s." % (self.disease, c["name"], self._rname(tgt)),
                               imp=(tgt["owner"] == self.player), kind="land")

    # ------------------------------------------------------------ medicine
    def _medicine(self):
        tier = self.tech["medicine"]
        self.med_sales_today = 0.0
        if tier < 3:
            return
        self.med_stock = min(MED_CAP, self.med_stock + MED_PRODUCTION[tier] * self._mod("pharma_lease", 0.7))
        if any(r["infection"] > 0.01 for r in self._my_regions()):
            self.med_stock = max(0.0, self.med_stock - (2 + 0.8 * tier))
        keep, price = {"keep": (MED_CAP, 0.0), "balanced": (200, 1.0), "sell": (40, 1.3)}[self.med_policy]
        if price and self.med_stock > keep:
            sold = self.med_stock - keep
            self.med_stock = keep
            self.med_sales_today = sold * price
            self.med_exported += int(sold)
            self.cure_progress = min(1.0, self.cure_progress + 0.00002 * sold * (1 if self.cure_started else 0))

    # ------------------------------------------------------------ diplomacy
    def _get_rel(self, a, b):
        key = tuple(sorted((a, b)))
        if key not in self.ai_rel:
            self.ai_rel[key] = float(random.Random(a + b + str(self.start_date)).randint(-25, 22))
        return self.ai_rel[key]

    def _change_rel(self, cid, delta):
        if cid in self.rel:
            self.rel[cid] = _clamp(self.rel[cid] + delta, -100, 100)

    def _diplomacy_drift(self):
        for cid in self.rel:
            if self.countries[cid]["merged"]:
                continue
            if cid in self.war:
                self.rel[cid] = min(self.rel[cid], -55.0)
                continue
            target = self.rel_base[cid] + (30 if cid in self.trade else 0) + (35 if cid in self.allied else 0)
            self.rel[cid] += _clamp(target - self.rel[cid], -0.05, 0.05 if cid not in self.trade else 0.08)
            if cid in self.allied:
                self.rel[cid] = max(self.rel[cid], 55.0)

    def _provoke(self, victim, region):
        """Attacking a country: war, grudges, and a bad name in the world."""
        if victim not in self.countries:
            return
        self.war.add(victim)
        self.war_since.setdefault(victim, self.turn)
        self.trade.discard(victim)
        self.rel[victim] = min(self.rel[victim], -75.0)
        for n in self.countries[victim]["borders"]:
            if n in self.rel and n not in self.war:
                self._change_rel(n, -8)
        for cid in self.rel:
            if cid not in self.war:
                self._change_rel(cid, -2)

    def _ai_war_daily(self):
        for cid in list(self.war):
            c = self.countries[cid]
            if self.turn < c["next_attack"] or self.turn < self.truce.get(cid, 0):
                continue
            P = PERSONALITY[c["personality"]]
            c["next_attack"] = self.turn + (26 if P["aggr"] else 40) + self.rng.randint(0, 14)
            mine = self._my_regions()
            stolen = [r for r in mine if r["country_id"] == cid]
            adj = [r for r in mine if r["country_id"] in c["borders"]]
            pool = stolen or adj
            if not pool:
                continue
            tgt = self.rng.choice(pool)
            share = tgt["population"] / max(1.0, self._owned_pop.get(self.player, 1.0))
            ratio = self.power(cid) * 0.9 / max(1e-6, self.power(self.player) * (0.35 + 0.65 * share) * (1 + 0.1 * self.tech["military"]))
            if self.rng.random() < _clamp(ratio / (ratio + 1), 0.05, 0.85):
                tgt["owner"], tgt["lockdown"], tgt["cordon"], tgt["unrest"] = cid, False, False, 0.0
                self.stability = max(0.0, self.stability - 0.02)
                self._news("%s attacks and takes %s from you!" % (c["name"], self._rname(tgt)), imp=True, kind="dip")
            else:
                self._news("%s's attack on %s was repelled." % (c["name"], self._rname(tgt)), imp=True, kind="dip")

    # ------------------------------------------------------------ every country runs its own engine
    def _ai_daily(self):
        for cid, c in self.countries.items():
            if cid == self.player or c["merged"]:
                continue
            if (self.turn + c["idx"]) % 7 == 0:
                self._ai_week(cid, c)
        self._ai_war_daily()

    def _ai_week(self, cid, c):
        P = PERSONALITY[c["personality"]]
        owned = self._by_owner.get(cid, [])
        own = self._cinf.get(cid, 0.0)
        # --- economy and investment
        income = INCOME_SCALE * (3 + sum(self._region_value(r) for r in owned)) * ECON_MULT[c["tech"]["economy"]] * (0.6 + 0.8 * c["stability"])
        c["budget"] += 7 * income
        options = [(t, w) for t, w in P["spend"].items() if c["tech"][t] < 5]
        if options:
            track = self.rng.choices([t for t, _ in options], weights=[w for _, w in options])[0]
            cost = TIER_COST[c["tech"][track]] * 0.9
            if c["budget"] >= cost:
                c["budget"] -= cost
                c["tech"][track] += 1
                tier = c["tech"][track]
                if tier >= 4 and track in ("medicine", "military", "research") and self.rng.random() < 0.6:
                    txt = {"medicine": "opens a national vaccine programme", "military": "expands its armed forces",
                           "research": "funds a cure research institute"}[track]
                    self._news("%s %s." % (c["name"], txt), kind="ai")
        # --- borders
        near = any(self._cinf.get(n, 0.0) > 0.01 for n in c["borders"])
        want = (own >= P["close"] and own > 0) or (P["paranoid"] and (self.cure_started or near))
        closed = not (c["air_open"] and c["sea_open"])
        if closed:
            if self.turn - c["closed_turn"] >= P["reopen"] and ((not want) or own > 0.7 or c["stability"] < 0.25):
                c["air_open"] = c["sea_open"] = True
                if self.rng.random() < 0.5:
                    self._news("%s reopens its airports and ports." % c["name"], kind="ai")
        elif want and own <= 0.7 and c["stability"] > 0.15:      # past 70% infected, closing is pointless
            c["air_open"] = c["sea_open"] = False
            c["closed_turn"] = self.turn
            self._news("%s closes its airports and ports." % c["name"], kind="ai")
        if c["land_open"] and near and (P["paranoid"] or own > 0.3) and c["borders"]:
            c["land_open"] = False
        elif not c["land_open"] and not near and own < 0.05:
            c["land_open"] = True
        # --- regional lockdowns
        for r in owned:
            if not r["lockdown"] and r["infection"] >= P["lock"] and c["stability"] >= 0.2 and own < 0.8:
                r["lockdown"] = True
                if self.rng.random() < 0.4:
                    self._news("%s locks down %s." % (c["name"], r["name"]), kind="ai")
            elif r["lockdown"] and (r["infection"] < 0.01 or own > 0.85):
                r["lockdown"] = False
        # --- ambition
        if P["aggr"] <= 0 or cid in self.allied:
            return
        if cid not in self.war and self.rel.get(cid, 0) < -10 and any(n in c["borders"] for n in self._my_cids) \
                and self.power(cid) * 0.8 > self.power(self.player) * 0.5 and self.rng.random() < 0.05 * P["aggr"]:
            self.war.add(cid)
            self.war_since[cid] = self.turn
            self.rel[cid] = min(self.rel[cid], -70.0)
            c["next_attack"] = self.turn + 25
            self._news("WAR: %s declares war on %s!" % (c["name"], self._cname(self.player)), imp=True, kind="dip")
            return
        if self.rng.random() < 0.012 * P["aggr"]:                # AI against AI
            rivals = [n for n in c["borders"] if n != self.player and n in self.countries and not self.countries[n]["merged"]
                      and self._by_owner.get(n) and self._get_rel(cid, n) < 0 and self.power(cid) > 1.6 * self.power(n)]
            if rivals:
                n = self.rng.choice(rivals)
                r = self.rng.choice(self._by_owner[n])
                self.ai_rel[tuple(sorted((cid, n)))] = -70.0
                if r["owner"] != cid:
                    r["owner"], r["lockdown"], r["cordon"] = cid, False, False
                    self._news("%s seizes %s from %s." % (c["name"], r["name"], self._cname(n)), kind="ai")

    # ------------------------------------------------------------ cure
    def _cure_phase(self):
        infected = [cid for cid in self.countries if self._cinf.get(cid, 0.0) >= 0.003]
        if not self.cure_started and (len(infected) >= 3 or self._world_share >= 0.01 or self.turn >= DETECT_DAY):
            self.cure_started = True
            closed = 0
            for cid in infected:
                c = self.countries[cid]
                if cid != self.player and cid not in self.union and (c["air_open"] or c["sea_open"]):
                    c["air_open"] = c["sea_open"] = False
                    c["closed_turn"] = self.turn
                    closed += 1
            self._news("WHO confirms the outbreak: global cure research begins. %d countries ground flights." % closed, imp=True, kind="cure")
        if not self.cure_started:
            return
        contrib = 0.0
        for cid, c in self.countries.items():
            if c["merged"]:
                continue
            contrib += CURE_COUNTRY * c["stability"] * math.sqrt(c["population"] / 1e6) * (0.2 + self._cinf.get(cid, 0.0)) \
                * (1 + 0.25 * c["tech"]["research"])
        rate = (CURE_BASE + contrib) * (1 + RESEARCH_CURE[self.tech["research"]]) / self.D["cure"]
        self.cure_progress = min(1.0, self.cure_progress + rate)
        for m in (0.25, 0.5, 0.75):
            if self.cure_progress >= m and m not in self._milestones:
                self._milestones.add(m)
                self._news("Cure research reaches %d%%." % int(m * 100), imp=True, kind="cure")

    def _mutation_news(self):
        return None

    # ------------------------------------------------------------ the end
    def player_stats(self):
        mine = self._my_regions()
        pop = sum(r["population"] for r in mine)
        dead = sum(r["population"] * r["dead"] for r in mine)
        sick = sum(r["population"] * r["infection"] * (1 - r["dead"]) for r in mine)
        return {"regions": len(mine), "population": int(pop), "dead": int(dead), "infected": int(sick),
                "dead_share": (dead / pop) if pop else 1.0}

    def _check_end(self):
        s = self.player_stats()
        if self.cure_progress >= 1.0:
            self.cure_owner = "Global Coalition"
            self.game_over = True
            if s["dead_share"] < VICTORY_MAX_DEAD:
                self.result = "victory"
                self.result_text = "The cure is ready. %s survived with %.0f%% of its people alive." % (
                    self._cname(self.player), (1 - s["dead_share"]) * 100)
            else:
                self.result = "defeat"
                self.result_text = "The cure came too late: %.0f%% of your population died." % (s["dead_share"] * 100)
            self._news(("VICTORY: " if self.result == "victory" else "DEFEAT: ") + self.result_text, imp=True, kind="cure")
        elif s["regions"] == 0 or s["dead_share"] >= 0.92:
            self.game_over = True
            self.result = "defeat"
            self.result_text = "%s has collapsed. The disease won." % self._cname(self.player)
            self._news("DEFEAT: " + self.result_text, imp=True, kind="cure")

    # ------------------------------------------------------------ story: cards, phases, mutations, crises
    def _post(self, pending):
        self._uid += 1
        pending["uid"] = self._uid
        if self._pending is None:
            self._pending = pending
        else:
            self._queue.append(pending)
        self._stop = True

    def _card_once(self, key, title, text, mood="skull", news=None):
        if key in self._cards_done:
            return
        self._cards_done.add(key)
        self._post(events.card(key, title, text, mood))
        if news:
            self._news(news, imp=False, kind="cure")

    def _pathogen(self):
        n_inf = sum(1 for v in self._cinf.values() if v >= 0.003)
        ph = 1
        if n_inf >= 3:
            ph = 2
        if self._world_share >= 0.12 or n_inf >= 40:
            ph = 3
        if self._world_dead_share >= 0.03 or self._world_share >= 0.45:
            ph = 4
        if ph > self.phase:
            self.phase = ph
            text = {2: "%d countries are fighting %s. Borders are closing one by one and the first lockdowns have begun." % (n_inf, self.disease_name),
                    3: "%s is on every continent. Airports go dark, ships are turned away, and the news is all one story." % self.disease_name,
                    4: "Governments are failing. Cities are going quiet. Whatever you build now, you are building it in the ruins."}[ph]
            self._card_once("phase%d" % ph, PHASES[ph].upper(), text, {2: "outbreak", 3: "wave", 4: "skull"}[ph],
                            news="The disease enters a new phase: %s." % PHASES[ph])
        if self.turn >= self.next_mutation and len(self.mutations) < len(MUTATIONS):
            self._mutate()
        # milestones
        if self._cinf.get(self.player, 0.0) >= 0.001:
            self._card_once("home", "It has reached us", "The first patients are in %s. Whatever the plan was until now, it is a different plan now." % self._cname(self.player), "fire")
        if self._fallen:
            self._card_once("fallen", "A nation goes silent", "No broadcasts, no flights, no answer on any frequency. %s is gone." % self._cname(self._fallen[0]), "skull")
        for n, label in ((1e6, "A million graves"), (1e7, "Ten million"), (1e8, "A hundred million"), (1e9, "A billion")):
            if self._world_dead >= n:
                self._card_once("dead%d" % n, label.upper(), "The dead are no longer a number you can picture. Your own people are counting too.", "skull")
        if self.cure_progress >= 0.9:
            self._card_once("last", "The last stretch", "The cure is at 90%. Every day now is a day your people have to survive on their own.", "flask")

    def _mutate(self):
        key = self.rng.choice([k for k in MUTATIONS if k not in self.mutations])
        name, text, fx = MUTATIONS[key]
        self.mutations.append(key)
        for k, v in fx.items():
            if k == "leak":
                self.mm["leak"] += v
            else:
                self.mm[k] *= v
        self.next_mutation = self.turn + self.rng.randint(60, 100)
        self._news("MUTATION: %s turns %s." % (self.disease_name, name.lower()), imp=False, kind="cure")
        self._post(events.card("mut_" + key, "Mutation: " + name, text, "flask"))

    def _events_phase(self):
        if self._pending or self.turn < 10 or self.turn < self.next_event:
            return
        pool = events.eligible(self)
        if not pool:
            self.next_event = self.turn + 4
            return
        i = self.rng.choices(range(len(pool)), weights=[w for w, _, _ in pool])[0]
        _, ev, ctx = pool[i]
        self._post(events.build(self, ev, ctx))
        self.next_event = self.turn + self.rng.randint(7, 14)

    # ------------------------------------------------------------ one day
    def _day(self):
        self.turn += 1
        self.traffic = []
        self._adapt = 1 + 1.5 * min(1.0, self.turn / 540)
        self.income = self._economy()
        self._player_stability()
        self._grow()
        self._refresh_cache()
        self._domestic_spread()
        self._spread_air_sea("air")
        self._spread_air_sea("sea")
        self._spread_land()
        self._medicine()
        self._diplomacy_drift()
        self._ai_daily()
        self._cure_phase()
        self._pathogen()
        self._events_phase()
        self._check_end()

    def tick(self, days=1):
        """Advance up to `days` days; stops early when something important happens."""
        done = 0
        self._stop = False
        while done < days and not self.game_over:
            self._day()
            done += 1
            if self._stop:
                break
        return done

    # ------------------------------------------------------------ player actions
    def _set_channels(self, modes, closed):
        for cid in [self.player] + [u for u in self.union]:
            c = self.countries[cid]
            for m in modes:
                c[m + "_open"] = not closed
            c["closed_turn"] = self.turn

    def _set_borders(self, closed):
        self._set_channels(["air", "sea", "land"], closed)

    @property
    def closed_modes(self):
        c = self.countries[self.player]
        return [m for m in ("air", "sea", "land") if not c[m + "_open"]]

    @property
    def borders_closed(self):
        return len(self.closed_modes) == 3

    @property
    def borders_closed_any(self):
        return bool(self.closed_modes)

    def _mod(self, name, mult):
        """A temporary effect from a decision, active until its expiry day."""
        return mult if self.mods.get(name, -1) > self.turn else 1.0

    def do_action(self, action, payload=None):
        """Returns (ok, message). Only 'tick' advances time."""
        p = payload or {}
        if self.game_over and action != "tick":
            return False, "The game is over."
        self._new_news = []
        handler = getattr(self, "_act_" + action, None)
        if not handler:
            return False, "Unknown action: %s" % action
        ok, msg = handler(p)
        if ok:
            self._refresh_cache()
        return ok, msg

    def _act_tick(self, p):
        if self.game_over:
            return False, "The game is over."
        if self._pending:
            return False, "A decision is waiting for you."
        try:
            days = int(p.get("days", 1))
        except (TypeError, ValueError):
            days = 1
        self._ticked = self.tick(_clamp(days, 1, 30))
        return True, ""

    def _modes_from(self, p):
        m = p.get("mode")
        return [m] if m in ("air", "sea", "land") else ["air", "sea", "land"]

    def _act_close_borders(self, p):
        modes = [m for m in self._modes_from(p) if m not in self.closed_modes]
        if not modes:
            return False, "Those borders are already closed."
        self._set_channels(modes, True)
        what = "its borders, airports and seaports" if len(modes) == 3 else {"air": "its airports", "sea": "its seaports", "land": "its land borders"}[modes[0]]
        self._news("%s CLOSED %s." % (self._cname(self.player), what), kind="local")
        return True, "Closed. Each closed channel costs income and slowly eats stability."

    def _act_open_borders(self, p):
        modes = [m for m in self._modes_from(p) if m in self.closed_modes]
        if not modes:
            return False, "Those borders are already open."
        self._set_channels(modes, False)
        what = "its borders, airports and seaports" if len(modes) == 3 else {"air": "its airports", "sea": "its seaports", "land": "its land borders"}[modes[0]]
        self._news("%s REOPENED %s." % (self._cname(self.player), what), kind="local")
        return True, "Reopened."

    def _act_choose(self, p):
        pend = self._pending
        if not pend:
            return False, "There is nothing to decide."
        try:
            idx = int(p.get("index", -1))
        except (TypeError, ValueError):
            idx = -1
        ok, msg = events.resolve(self, pend, idx)
        if not ok:
            return False, msg
        if pend["kind"] == "event":
            self._news("%s: %s" % (pend["title"], msg), kind="local")
        self._pending = self._queue.pop(0) if self._queue else None
        return True, msg

    def auto_resolve(self):
        """For tests and bots: take a random affordable option of whatever is pending."""
        while self._pending:
            opts = [c["i"] for c in self._pending["choices"] if c["cost"] <= self.budget] or [0]
            ok, _ = self.do_action("choose", {"index": self.rng.choice(opts)})
            if not ok:
                self._pending = self._queue.pop(0) if self._queue else None

    def _act_upgrade(self, p):
        track = p.get("track") or p.get("upgrade_type")
        track = {"science": "research"}.get(track, track)
        if track not in TRACKS:
            return False, "Unknown upgrade."
        tier = self.tech[track]
        if tier >= 5:
            return False, "%s is fully upgraded." % TRACKS[track][0]
        cost = TIER_COST[tier]
        if self.budget < cost:
            return False, "Not enough budget (need $%d)." % cost
        self.budget -= cost
        self.tech[track] = tier + 1
        name = TRACKS[track][2][tier][0]
        self._news("You built: %s (%s level %d)." % (name, TRACKS[track][0], tier + 1), kind="local")
        return True, "%s built." % name

    def _act_medicine_policy(self, p):
        pol = p.get("policy")
        if pol not in ("keep", "balanced", "sell"):
            return False, "Unknown policy."
        self.med_policy = pol
        return True, {"keep": "Doses stay at home.", "balanced": "Surplus doses are sold.", "sell": "Everything above the reserve is sold."}[pol]

    def _own_region(self, rid):
        r = self.regions.get(rid)
        if not r:
            return None, "Unknown region."
        if r["owner"] != self.player:
            return None, "That region is not yours."
        return r, ""

    def _act_lockdown(self, p):
        r, err = self._own_region(p.get("region_id"))
        if not r:
            return False, err
        on = p.get("on", not r["lockdown"])
        if on and self.budget < 10:
            return False, "A lockdown costs $10 to set up."
        if on:
            self.budget -= 10
        r["lockdown"] = bool(on)
        if not on:
            r["cordon"] = False
        self._news("%s %s %s." % (self._cname(self.player), "locks down" if on else "lifts the lockdown in", self._rname(r)), kind="local")
        return True, "Lockdown %s in %s." % ("ON" if on else "lifted", r["name"])

    def _act_evacuate(self, p):
        a, err = self._own_region(p.get("from_region"))
        if not a:
            return False, err
        b, err = self._own_region(p.get("to_region"))
        if not b:
            return False, err
        if a is b or a["infection"] < 0.01:
            return False, "Pick an infected region and a different, safe destination."
        if b["infection"] >= 0.03 or b["purged"]:
            return False, "The destination is not safe."
        cost = int(15 + a["population"] / 1e6 * 6)
        if self.budget < cost:
            return False, "Evacuation costs $%d." % cost
        self.budget -= cost
        moved = a["population"] * (1 - a["infection"]) * (1 - a["dead"]) * 0.5
        a["population"] -= moved
        b["population"] += moved
        risk = _clamp(0.18 + 0.55 * a["infection"] - 0.06 * self.tech["medicine"], 0.04, 0.9)
        if self.rng.random() < risk:
            b["infection"] = max(b["infection"], 0.002)
            self._news("Evacuees from %s brought the infection to %s." % (a["name"], self._rname(b)), imp=True, kind="local")
            return True, "%s people moved, but one or two of them were already infected." % format(int(moved), ",")
        self._news("%s people evacuated from %s to %s." % (format(int(moved), ","), a["name"], b["name"]), kind="local")
        return True, "%s people moved to %s. Nobody was infected." % (format(int(moved), ","), b["name"])

    def _act_cordon(self, p):
        r, err = self._own_region(p.get("region_id"))
        if not r:
            return False, err
        on = p.get("on", not r["cordon"])
        if on and not p.get("confirm"):
            return False, "Confirmation required."
        if on and r["infection"] < 0.02:
            return False, "Nothing to contain here yet."
        r["cordon"] = bool(on)
        if on:
            r["lockdown"] = True
            self._change_rel_all(-3)
            self.stability = max(0.0, self.stability - 0.04)
            self._news("%s imposes a lethal cordon on %s." % (self._cname(self.player), self._rname(r)), imp=False, kind="local")
        else:
            self._news("The cordon around %s is lifted." % self._rname(r), kind="local")
        return True, "Lethal cordon %s." % ("ON" if on else "lifted")

    def _act_purge(self, p):
        r, err = self._own_region(p.get("region_id"))
        if not r:
            return False, err
        if not p.get("confirm"):
            return False, "Confirmation required."
        if r["infection"] < 0.05:
            return False, "Only an infected region can be purged."
        r["dead"], r["infection"], r["purged"] = 0.98, 0.0, True
        r["lockdown"] = r["cordon"] = False
        self.stability = max(0.0, self.stability - 0.20)
        self._change_rel_all(-12)
        self._news("%s destroys %s to stop the disease. The world is horrified." % (self._cname(self.player), self._rname(r)), imp=False, kind="local")
        return True, "%s was wiped out. The infection is gone, and so are its people." % r["name"]

    def _change_rel_all(self, delta):
        for cid in self.rel:
            if cid not in self.war:
                self._change_rel(cid, delta)

    def _act_invade(self, p):
        r = self.regions.get(p.get("region_id"))
        if not r:
            return False, "Unknown region."
        if r["owner"] == self.player:
            return False, "You already control this region."
        defender = r["owner"]
        if defender in self.allied:
            return False, "%s is your ally." % self._cname(defender)
        if self.turn < self.truce.get(defender, 0):
            return False, "A ceasefire with %s is in force." % self._cname(defender)
        cost, chance, _ = self.invade_info(r)
        if self.budget < cost:
            return False, "Not enough budget (need $%d)." % cost
        self.budget -= cost
        self._provoke(defender, r)
        if self.rng.random() < chance:
            r["owner"], r["unrest"], r["lockdown"], r["cordon"] = self.player, 0.25, False, False
            self.conquests += 1
            self.stability = max(0.0, self.stability - 0.02)
            self._news("INVASION: %s captured %s! %s is now at war with you." % (self._cname(self.player), self._rname(r), self._cname(defender)), kind="dip")
            return True, "%s is yours. Expect revenge from %s." % (r["name"], self._cname(defender))
        self._news("The invasion of %s failed. %s is at war with you." % (self._rname(r), self._cname(defender)), kind="dip")
        return True, "The invasion failed, and %s is now at war with you." % self._cname(defender)

    # ---- diplomacy actions
    def _target(self, p):
        cid = p.get("country")
        if cid not in self.countries or cid == self.player or self.countries[cid]["merged"]:
            return None
        return cid

    def _act_improve(self, p):
        cid = self._target(p)
        if not cid:
            return False, "Unknown country."
        if cid in self.war:
            return False, "You are at war. Seek a ceasefire first."
        if self.turn < self.cooldown.get((cid, "improve"), 0):
            return False, "They need time before another gesture."
        if self.budget < 40:
            return False, "A diplomatic gift costs $40."
        self.budget -= 40
        self.cooldown[(cid, "improve")] = self.turn + 14
        self._change_rel(cid, 8)
        return True, "Relations with %s improve." % self._cname(cid)

    def _act_trade(self, p):
        cid = self._target(p)
        if not cid:
            return False, "Unknown country."
        if self.tech["economy"] < 2:
            return False, "Build Trade hubs (Economy level 2) first."
        if cid in self.trade:
            return False, "You already trade with them."
        if cid in self.war or self.rel[cid] < 25:
            return False, "They do not trust you enough (relations 25+)."
        if self.budget < 60:
            return False, "A trade agreement costs $60."
        self.budget -= 60
        self.trade.add(cid)
        self._news("%s and %s sign a trade agreement." % (self._cname(self.player), self._cname(cid)), kind="dip")
        return True, "Trade agreement signed with %s." % self._cname(cid)

    def _act_alliance(self, p):
        cid = self._target(p)
        if not cid:
            return False, "Unknown country."
        if cid in self.allied:
            return False, "Already allies."
        if cid not in self.trade or self.rel[cid] < 60:
            return False, "An alliance needs a trade agreement and relations 60+."
        if self.budget < 120:
            return False, "An alliance costs $120."
        self.budget -= 120
        self.allied.add(cid)
        self._change_rel(cid, 10)
        self._news("ALLIANCE: %s and %s join forces." % (self._cname(self.player), self._cname(cid)), kind="dip")
        return True, "%s is now your ally. They will not attack you and they share your medicine." % self._cname(cid)

    def _act_union(self, p):
        cid = self._target(p)
        if not cid:
            return False, "Unknown country."
        if cid not in self.allied or self.rel[cid] < 80:
            return False, "A union needs an alliance and relations 80+."
        cost = int(250 + self.countries[cid]["population"] / 1e6 * 2)
        if self.budget < cost:
            return False, "A union costs $%d." % cost
        self.budget -= cost
        c = self.countries[cid]
        for r in list(self._by_owner.get(cid, [])):
            r["owner"] = self.player
            r["lockdown"], r["cordon"], r["unrest"] = False, False, 0.0
        c["merged"] = True
        self.union.add(cid)
        self.allied.discard(cid)
        self.trade.discard(cid)
        pc = self.countries[self.player]
        c["air_open"], c["sea_open"], c["land_open"] = pc["air_open"], pc["sea_open"], pc["land_open"]
        self.stability = max(0.0, self.stability - 0.03)
        self._news("UNION: %s and %s merge into a single, larger state!" % (self._cname(self.player), c["name"]), imp=True, kind="dip")
        return True, "%s joined your country. Their regions, good and bad, are now yours." % c["name"]

    def _act_ceasefire(self, p):
        cid = self._target(p)
        if not cid:
            return False, "Unknown country."
        if cid not in self.war:
            return False, "You are not at war with them."
        stolen = sum(1 for r in self._my_regions() if r["country_id"] == cid)
        cost = 60 + 25 * stolen
        if self.budget < cost:
            return False, "A peace offer costs $%d." % cost
        if self.turn < self.cooldown.get((cid, "peace"), 0):
            return False, "They refuse to talk yet."
        self.budget -= cost
        chance = _clamp(0.3 + 0.004 * (self.rel[cid] + 100) - 0.12 * stolen + 0.002 * (self.turn - self.war_since.get(cid, self.turn)), 0.1, 0.85)
        if self.rng.random() < chance:
            self.war.discard(cid)
            self.war_since.pop(cid, None)
            self.truce[cid] = self.turn + 60
            self.rel[cid] = -35.0
            self._news("CEASEFIRE: %s and %s stop fighting." % (self._cname(self.player), self._cname(cid)), kind="dip")
            return True, "%s accepts a ceasefire for 60 days." % self._cname(cid)
        self.cooldown[(cid, "peace")] = self.turn + 10
        return True, "%s rejected your offer." % self._cname(cid)

    def _act_sell_medicine(self, p):
        cid = self._target(p)
        if not cid:
            return False, "Unknown country."
        if self.tech["medicine"] < 3:
            return False, "Build Pharma factories (Medicine level 3) first."
        if cid in self.war:
            return False, "You do not sell medicine to an enemy."
        if self.med_stock < 60:
            return False, "You need 60 doses in stock."
        price = 1.3 + 2.0 * self._cinf.get(cid, 0.0)
        self.med_stock -= 60
        self.med_exported += 60
        self.budget += 60 * price
        self.countries[cid]["aid_until"] = self.turn + 90
        self._change_rel(cid, 10)
        self.cure_progress = min(1.0, self.cure_progress + 0.0012 * (1 if self.cure_started else 0))
        self._news("%s buys medicine from %s." % (self._cname(cid), self._cname(self.player)), kind="dip")
        return True, "Sold 60 doses to %s for $%d." % (self._cname(cid), int(60 * price))

    # ------------------------------------------------------------ read-only views
    def region_info(self, rid):
        r = self.regions.get(rid)
        if not r:
            return None
        info = {"id": rid, "owner": r["owner"], "mine": r["owner"] == self.player}
        if r["owner"] != self.player:
            cost, chance, adj = self.invade_info(r)
            info.update(invade_cost=cost, invade_chance=round(chance, 2), adjacent=adj,
                        at_war=r["owner"] in self.war, allied=r["owner"] in self.allied,
                        blocked="" if r["owner"] not in self.allied else "ally",
                        truce=self.turn < self.truce.get(r["owner"], 0))
        else:
            info["evac_targets"] = [{"id": x["id"], "name": x["name"]} for x in self._my_regions()
                                    if x["id"] != rid and x["infection"] < 0.03 and not x["purged"]]
            info["evac_cost"] = int(15 + r["population"] / 1e6 * 6)
            info["unrest"] = round(r["unrest"], 2)
        return info

    def diplomacy(self):
        out = []
        my_pow = self.power(self.player)
        for cid, c in self.countries.items():
            if cid == self.player or c["merged"]:
                continue
            rel = self.rel[cid]
            war = cid in self.war
            stolen = sum(1 for r in self._my_regions() if r["country_id"] == cid)
            out.append({
                "id": cid, "name": c["name"], "relation": int(round(rel)), "war": war, "ally": cid in self.allied,
                "trade": cid in self.trade, "neighbour": cid in self._my_cids or any(n in self._my_cids for n in c["borders"]),
                "infection": round(self._cinf.get(cid, 0.0), 3), "personality": PERSONALITY[c["personality"]]["label"],
                "power": round(self.power(cid) / max(1.0, my_pow), 2), "stolen": stolen,
                "truce": max(0, self.truce.get(cid, 0) - self.turn),
                "ceasefire_cost": 60 + 25 * stolen,
                "can": {
                    "improve": (not war) and self.turn >= self.cooldown.get((cid, "improve"), 0),
                    "trade": (not war) and cid not in self.trade and self.rel[cid] >= 25 and self.tech["economy"] >= 2,
                    "alliance": cid in self.trade and cid not in self.allied and self.rel[cid] >= 60,
                    "union": cid in self.allied and self.rel[cid] >= 80,
                    "ceasefire": war,
                    "sell": (not war) and self.tech["medicine"] >= 3 and self.med_stock >= 60,
                },
            })
        out.sort(key=lambda x: (not x["war"], not x["ally"], not x["neighbour"], x["name"]))
        return out

    # ------------------------------------------------------------ serialisation
    def _player_block(self):
        return {
            "budget": int(self.budget), "income": self.income, "income_detail": self.income_detail,
            "tech": dict(self.tech), "stability": round(self.stability, 3), "borders_closed": self.borders_closed,
            "closed": self.closed_modes,
            "power": round(self.power(self.player), 1), "conquests": self.conquests,
            "medicine": {"stock": int(self.med_stock), "production": MED_PRODUCTION[self.tech["medicine"]],
                         "policy": self.med_policy, "exported": self.med_exported,
                         "resistance": round(self._resistance(self.player), 2)},
            "war": sorted(self.war), "allied": sorted(self.allied), "trade": sorted(self.trade), "union": sorted(self.union),
        }

    def _public_pending(self):
        p = self._pending
        if not p:
            return None
        out = {k: v for k, v in p.items() if not k.startswith("_")}
        out["choices"] = [dict(c, can=self.budget >= c["cost"]) for c in p["choices"]]
        out["queued"] = len(self._queue)
        return out

    def _common(self):
        self.income = self._economy(apply=False)          # always show today's real income
        return {
            "started": True, "turn": self.turn, "day": self.turn, "date": self.date_of().isoformat(),
            "start_date": self.start_date.isoformat(),
            "disease": self.disease, "disease_name": self.disease_name,
            "player_country": self.player, "player": self._player_block(),
            "budget": int(self.budget), "income": self.income, "stability": round(self.stability, 3),
            "borders_closed": self.borders_closed,
            "military_level": self.tech["military"], "science_level": self.tech["research"],
            "cure_progress": round(self.cure_progress, 4), "cure_started": self.cure_started, "cure_owner": self.cure_owner,
            "world_infected": round(self._world_share, 4), "player_stats": self.player_stats(),
            "news_new": list(self._new_news), "traffic": self.traffic,
            "game_over": self.game_over, "result": self.result, "result_text": self.result_text,
            "stopped_early": self._stop,
            "phase": self.phase, "phase_name": PHASES[self.phase],
            "mutations": [{"key": k, "name": MUTATIONS[k][0], "text": MUTATIONS[k][1]} for k in self.mutations],
            "world_dead": int(self._world_dead), "world_dead_share": round(self._world_dead_share, 4),
            "fallen": len(self._fallen), "infected_countries": sum(1 for v in self._cinf.values() if v >= 0.003),
            "mods": [{"name": events.MOD_LABELS[k][0], "text": events.MOD_LABELS[k][1], "days": v - self.turn}
                     for k, v in self.mods.items() if v > self.turn and k in events.MOD_LABELS],
            "pending": self._public_pending(),
        }

    def summary(self, full=True):
        d = self._common()
        regions = []
        for r in self.regions.values():
            row = {"id": r["id"], "infection": round(r["infection"], 4), "dead": round(r["dead"], 3), "owner": r["owner"],
                   "lockdown": 2 if r["cordon"] else (1 if r["lockdown"] else 0), "population": int(r["population"]),
                   "unrest": round(r["unrest"], 2)}
            if full:
                row.update(country_id=r["country_id"], name=r["name"], lat=r["lat"], lng=r["lng"], control="country")
            regions.append(row)
        countries = []
        for c in self.countries.values():
            row = {"id": c["id"], "infection": round(self._cinf.get(c["id"], 0.0), 3), "air_open": c["air_open"],
                   "sea_open": c["sea_open"], "land_open": c["land_open"], "merged": c["merged"],
                   "war": c["id"] in self.war, "ally": c["id"] in self.allied, "trade": c["id"] in self.trade}
            if full:
                row.update(iso3=c["iso3"], name=c["name"], capital_name=c["capital_name"], population=c["population"],
                           stability=c["stability"], has_port=c["has_port"], has_airport=c["has_airport"],
                           ports=c["port_pos"][:8], personality=PERSONALITY[c["personality"]]["label"] if c["id"] != self.player else "You")
            countries.append(row)
        d.update(regions=regions, countries=countries, full=full)
        if full:
            d["log"] = self.log
            d["news"] = self.log[:5]
            d["tracks"] = {k: {"name": v[0], "text": v[1], "tiers": [{"name": t[0], "text": t[1], "cost": TIER_COST[i]} for i, t in enumerate(v[2])]}
                           for k, v in TRACKS.items()}
        return d