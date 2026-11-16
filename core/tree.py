"""
The upgrade tree.

Five tabs. Each has a trunk of five tiers (bought in order) and branches that hang off it.
Some branches are doctrines: you can take only one of each pair, so you have to pick a path.
Positions (x, y) are percentages of the drawing area on the upgrade screen.
"""

TIER_COST = [60, 120, 220, 380, 600]

TABS = {
    "economy": ("Economy", "Income, trade and how long your people will put up with you."),
    "military": ("Military", "Strength for campaigns, defence and keeping conquered land quiet."),
    "medicine": ("Medicine", "Protects your own people. From tier 3 you make doses to keep or sell."),
    "research": ("Research", "The global cure, and how well you cope with mutations."),
    "border": ("Border control", "Screening that works even with your borders open."),
}

TRUNK = {
    "economy": [("Tax reform", "Income +15%."), ("Trade hubs", "Income +30%. Unlocks trade agreements."),
                ("Industrial zones", "Income +50%."), ("Logistics network", "Income +75%."), ("Mega-economy", "Income +100%.")],
    "military": [("Barracks", "Army strength x1.25."), ("Munition factories", "Army strength x1.55."),
                 ("Armoured divisions", "Army strength x1.9."), ("Air force", "Army strength x2.3."), ("Rapid deployment", "Army strength x2.8.")],
    "medicine": [("Clinics", "5% harder to infect your people. Deaths -10%."), ("Testing and tracing", "12% harder to infect. Deaths -18%."),
                 ("Pharma factories", "25% harder to infect. Makes 6 doses a day to keep or sell."),
                 ("Vaccine lab", "40% harder to infect. 14 doses a day."), ("Mass immunisation", "60% harder to infect. 28 doses a day. Deaths -50%.")],
    "research": [("Research grants", "Global cure +6%."), ("University labs", "Global cure +13%."), ("Genome sequencing", "Global cure +21%."),
                 ("Global lab network", "Global cure +30%."), ("Cure breakthrough", "Global cure +40%.")],
    "border": [("Health screening", "20% of infected arrivals stopped by air and sea."), ("Port inspections", "40% stopped. Land 30%."),
               ("Border fences", "55% stopped. Land 50%."), ("Quarantine zones", "70% stopped. Land 65%."), ("Smart borders", "80% stopped. Land 75%.")],
}

TRUNK_X = [10, 30, 50, 70, 90]

# id, tab, name, text, cost, requires (node ids), exclusive group, x, y
BRANCHES = [
    # ---- medicine
    ("med_ships", "medicine", "Hospital ships", "Deaths -8% in your regions.", 110, ["medicine_2"], None, 30, 20),
    ("med_tests", "medicine", "Rapid tests", "10% fewer infected arrivals get through.", 110, ["medicine_2"], None, 30, 80),
    ("med_quar", "medicine", "Quarantine doctrine", "Lockdowns slow the disease more and cost 30% less. You cannot also take the Mobility doctrine.", 200, ["medicine_3"], "med_doc", 50, 18),
    ("med_mob", "medicine", "Mobility doctrine", "Closed borders cost 40% less income and stability. You cannot also take the Quarantine doctrine.", 200, ["medicine_3"], "med_doc", 50, 82),
    ("med_drive", "medicine", "Vaccination drive", "Your people are 8% harder to infect.", 320, ["medicine_4"], None, 70, 20),
    ("med_export", "medicine", "Export licences", "Your medicine sells for 30% more.", 180, ["medicine_3"], None, 70, 80),
    # ---- military
    ("mil_garr", "military", "Garrisons", "Occupied regions grow restless 25% slower.", 90, ["military_1"], None, 10, 22),
    ("mil_prop", "military", "Propaganda corps", "Invasions and war crimes cost you half as much standing abroad.", 160, ["military_2"], None, 30, 20),
    ("mil_sf", "military", "Special forces", "+8% chance in every campaign.", 140, ["military_2"], None, 30, 80),
    ("mil_blitz", "military", "Blitz doctrine", "Campaigns advance 40% faster, with heavier losses. You cannot also take the Fortress doctrine.", 220, ["military_3"], "mil_doc", 50, 18),
    ("mil_fort", "military", "Fortress doctrine", "Enemy attacks on you are 30% less likely to succeed. You cannot also take the Blitz doctrine.", 220, ["military_3"], "mil_doc", 50, 82),
    ("mil_vet", "military", "Veteran cadre", "Losses in campaigns -30%.", 340, ["military_4"], None, 70, 20),
    # ---- economy
    ("eco_fleet", "economy", "Trade fleets", "Trade partners pay you 50% more.", 110, ["economy_2"], None, 30, 20),
    ("eco_haven", "economy", "Free zones", "+$1.5 per day.", 110, ["economy_2"], None, 30, 80),
    ("eco_auster", "economy", "Austerity", "Income +15%, stability -0.04% a day. You cannot also take Stimulus.", 200, ["economy_3"], "eco_doc", 50, 18),
    ("eco_stim", "economy", "Stimulus", "Income -5%, stability +0.06% a day. You cannot also take Austerity.", 200, ["economy_3"], "eco_doc", 50, 82),
    ("eco_black", "economy", "Sanction busting", "Sanctions cost you half as much.", 260, ["economy_4"], None, 70, 20),
    # ---- research
    ("res_open", "research", "Open science", "Global cure +5%, and relations improve slowly with everyone. You cannot also take the National programme.", 180, ["research_2"], "res_doc", 30, 20),
    ("res_nat", "research", "National programme", "Your people are 6% harder to infect. You cannot also take Open science.", 180, ["research_2"], "res_doc", 30, 80),
    ("res_gen", "research", "Gene library", "Mutations hurt you 30% less.", 260, ["research_3"], None, 50, 20),
    ("res_fast", "research", "Emergency trials", "Global cure +8% faster.", 340, ["research_4"], None, 70, 20),
    # ---- border
    ("bor_bio", "border", "Biometrics", "Silent carriers hurt your border control half as much.", 140, ["border_2"], None, 30, 20),
    ("bor_coast", "border", "Coast guard", "15% fewer infected arrivals by sea.", 130, ["border_2"], None, 30, 80),
    ("bor_air", "border", "Airport quarantine", "15% fewer infected arrivals by air.", 220, ["border_3"], None, 50, 20),
    ("bor_wall", "border", "Fortified frontier", "20% fewer infected arrivals by land. You cannot also take Green corridors.", 240, ["border_3"], "bor_doc", 50, 82),
    ("bor_cor", "border", "Green corridors", "Closed borders cost 25% less income. You cannot also take the Fortified frontier.", 240, ["border_3"], "bor_doc", 70, 80),
]


def build_nodes():
    nodes = []
    for tab, tiers in TRUNK.items():
        for i, (name, text) in enumerate(tiers):
            nodes.append({"id": "%s_%d" % (tab, i + 1), "tab": tab, "name": name, "text": text, "cost": TIER_COST[i],
                          "req": ["%s_%d" % (tab, i)] if i else [], "excl": None, "x": TRUNK_X[i], "y": 50, "kind": "trunk", "tier": i + 1})
    for nid, tab, name, text, cost, req, excl, x, y in BRANCHES:
        nodes.append({"id": nid, "tab": tab, "name": name, "text": text, "cost": cost, "req": req, "excl": excl,
                      "x": x, "y": y, "kind": "branch", "tier": 0})
    return nodes


NODES = build_nodes()
BY_ID = {n["id"]: n for n in NODES}


def public():
    return {"tabs": {k: {"name": v[0], "text": v[1]} for k, v in TABS.items()}, "nodes": NODES}