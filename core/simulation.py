import random

from core.data_loader import load_all


DIFFICULTY = {
    "easy":       {"spread": 0.7, "ai": 0.5, "cure": 1.5, "border": 0.5, "diplo": 0.5},
    "medium":     {"spread": 1.0, "ai": 1.0, "cure": 1.0, "border": 1.0, "diplo": 1.0},
    "hard":       {"spread": 1.3, "ai": 1.5, "cure": 0.7, "border": 1.5, "diplo": 1.5},
    "extra_hard": {"spread": 1.6, "ai": 2.0, "cure": 0.5, "border": 2.0, "diplo": 2.0},
}


class Simulation:
    """Core, engine-agnostic simulation of Mode 1 (Survival / Eradication)."""

    def __init__(self, data_dir="data", disease_choice=None,
                 difficulty="medium", player_country="RO"):
        data = load_all(data_dir)
        self.countries = {c["id"]: c for c in data["countries"]}
        self.regions = {r["id"]: r for r in data["regions"]}
        self.infection_types = {t["id"]: t for t in data["infection_types"]}

        if difficulty not in DIFFICULTY:
            raise ValueError(f"Unknown difficulty: {difficulty}")
        self.difficulty = DIFFICULTY[difficulty]

        self.disease = self._pick_disease(disease_choice)
        self.player_country = player_country
        self.turn = 0
        self.cure_owner = None
        self.black_zone = {"controlled_regions": [], "strength": 0.0}
        self.log = []
        self.game_over = False
        self.result = None

        for c in self.countries.values():
            c.setdefault("borders_open", True)
            c.setdefault("cure_progress", 0.0)
            c.setdefault("collapsed", False)
            c.setdefault("control", "country")

    # ---------- setup ----------

    def _pick_disease(self, choice):
        if choice is None:
            choice = random.choice(list(self.infection_types.keys()))
        if choice not in self.infection_types:
            raise ValueError(f"Unknown disease: {choice}")
        return self.infection_types[choice]

    def _neighbors(self, region_id):
        return [
            self.regions[n]
            for n in self.regions[region_id]["adjacency"]
            if n in self.regions
        ]

    # ---------- main loop ----------

    def tick(self):
        if self.game_over:
            return self
        self.turn += 1
        self._phase_spread()
        self._phase_retreat()
        self._phase_combat()
        self._phase_cure()
        self._phase_black_zone()
        self._phase_diplomacy()
        self._check_end()
        return self

    def run(self, ticks=10):
        for _ in range(ticks):
            self.tick()
            if self.game_over:
                break
        return self

    # ---------- phases ----------

    def _phase_spread(self):
        key = self.disease["id"]
        base = 0.03 * self.disease["spread_speed"] * self.difficulty["spread"]

        for r in self.regions.values():
            if r["control"] == "black_zone":
                continue
            if r["control"] in ("abandoned", "infected") and r["population"] <= 0:
                continue
            vec = 1.0
            if "water" in self.disease["spread_vectors"] and "coast" in r["terrain"]:
                vec += 0.3
            if "air" in self.disease["spread_vectors"] and r["population"] > 1_000_000:
                vec += 0.2
            if "bite" in self.disease["spread_vectors"]:
                vec += 0.1
            r["infection"][key] = min(1.0, r["infection"][key] + base * vec)

        if self.disease.get("reanimation"):
            for r in self.regions.values():
                if r["infection"][key] >= 0.95 and r["control"] != "black_zone":
                    r["infection"]["zombie"] = min(1.0, r["infection"]["zombie"] + 0.05)

    def _phase_retreat(self):
        for r in list(self.regions.values()):
            if r["control"] != "country":
                continue
            if max(r["infection"].values()) < 0.70:
                continue

            same_country = [
                x for x in self.regions.values()
                if x["country_id"] == r["country_id"]
                and x["id"] != r["id"]
                and x["control"] == "country"
                and max(x["infection"].values()) < 0.30
            ]
            if same_country:
                target = max(same_country, key=lambda x: x["population"])
                target["population"] += r["population"]
                r["stay_behind_population"] = int(r["population"] * 0.10)
                r["population"] = r["stay_behind_population"]
                r["control"] = "abandoned"
                self._log(f"{r['id']} retreated internally to {target['id']}")
                continue

            moved = False
            for n in self._neighbors(r["id"]):
                neighbor_country = self.countries[n["country_id"]]
                if neighbor_country.get("borders_open", True) and n["control"] == "country":
                    n["population"] += r["population"]
                    r["stay_behind_population"] = 0
                    r["population"] = 0
                    r["control"] = "abandoned"
                    self._log(f"{r['id']} refugees crossed into {n['id']}")
                    moved = True
                    break
            if not moved:
                r["stay_behind_population"] = 0
                r["population"] = 0
                r["control"] = "infected"
                self._log(f"{r['id']} fell to infection (no safe retreat)")

    def _phase_combat(self):
        # Placeholder for player combat / invasion actions. See SPEC TBD.
        pass

    def _phase_cure(self):
        for c in self.countries.values():
            if c["collapsed"]:
                continue
            regions = [r for r in self.regions.values() if r["country_id"] == c["id"]]
            if not regions:
                continue
            industry = sum(r["resources"]["industry"] for r in regions) / len(regions)
            c["cure_progress"] = min(
                1.0,
                c["cure_progress"] + 0.01 * industry * self.difficulty["cure"]
            )
            if c["cure_progress"] >= 1.0 and self.cure_owner is None:
                self.cure_owner = c["id"]
                self._log(f"{c['id']} developed the cure")

    def _phase_black_zone(self):
        for c in self.countries.values():
            if c["collapsed"]:
                for r in self.regions.values():
                    if r["country_id"] == c["id"]:
                        r["control"] = "black_zone"

        for r in self.regions.values():
            if r["control"] == "infected":
                r["control"] = "black_zone"

        bz = [r for r in self.regions.values() if r["control"] == "black_zone"]
        self.black_zone["controlled_regions"] = [r["id"] for r in bz]
        self.black_zone["strength"] = 0.1 * len(bz)

        if len(bz) >= 5:
            for r in list(bz):
                for n in self._neighbors(r["id"]):
                    if n["control"] in ("abandoned", "infected") and n["id"] not in self.black_zone["controlled_regions"]:
                        n["control"] = "black_zone"

    def _phase_diplomacy(self):
        # Placeholder for land-back requests, alliances, wars. See SPEC TBD.
        pass

    def _check_end(self):
        total_inf = sum(max(r["infection"].values()) for r in self.regions.values())
        if total_inf <= 0.001 and self.cure_owner is not None:
            self.game_over = True
            self.result = "win_eradicated"
            self._log("WIN: disease eradicated")
            return
        if len(self.black_zone["controlled_regions"]) >= len(self.regions) * 0.5:
            self.game_over = True
            self.result = "lose_black_zone"
            self._log("LOSE: Black Zone dominates")
            return
        pc = self.countries.get(self.player_country)
        if pc and pc.get("collapsed"):
            self.game_over = True
            self.result = "lose_collapsed"
            self._log("LOSE: player country collapsed")

    # ---------- utils ----------

    def _log(self, msg):
        self.log.append(f"[T{self.turn}] {msg}")

    def summary(self):
        inf = sum(max(r["infection"].values()) for r in self.regions.values()) / max(1, len(self.regions))
        return {
            "turn": self.turn,
            "disease": self.disease["id"],
            "avg_infection": round(inf, 4),
            "black_zone_regions": len(self.black_zone["controlled_regions"]),
            "cure_owner": self.cure_owner,
            "game_over": self.game_over,
            "result": self.result,
        }
