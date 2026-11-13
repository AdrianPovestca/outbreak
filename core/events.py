"""
Crisis events for Outbreak.

Every so often the game stops and puts a decision in front of you. Each choice costs something:
money, stability, standing abroad, or lives. Nothing here is free.

An event is a dict:
    id, title, mood      mood picks the emblem / colours in the UI
    cond(sim)            returns a context dict if the event can happen now, else None
    text                 format string, filled with the context
    choices              list of dict(label, hint, cost, fn(sim, ctx) -> message, ok(sim, ctx) -> reason or None)
"""

# temporary effects: name -> (label, short description)
MOD_LABELS = {
    "hosp_a": ("Field hospitals", "Deaths -30% in your regions"),
    "hosp_b": ("Triage", "Deaths -15% in your regions"),
    "ration": ("Rationing", "Income -10%"),
    "junta": ("Generals' share", "Income -10%"),
    "army_weak": ("Purged army", "Army strength -20%"),
    "pharma_lease": ("Exclusive licence", "Medicine output -30%"),
    "volunteers": ("Volunteers", "Deaths -10% in your regions"),
    "screening": ("Airport screening", "Infected arrivals -30%"),
}


# ------------------------------------------------------------------ helpers
def _stab(sim, d):
    sim.stability = max(0.0, min(1.0, sim.stability + d))


def _mine(sim):
    return sim._my_regions()


def _closed_days(sim):
    c = sim.countries[sim.player]
    return sim.turn - c["closed_turn"] if sim.borders_closed_any else 0


def _neighbours_in_trouble(sim, level=0.2):
    out = []
    for n in sim.countries[sim.player]["borders"]:
        if n in sim.rel and not sim.countries[n]["merged"] and sim._cinf.get(n, 0) >= level and n not in sim.war:
            out.append(n)
    return out


# ------------------------------------------------------------------ 1. panic
def _panic_cond(sim):
    c = [r for r in _mine(sim) if r["infection"] >= 0.15 and (r["lockdown"] or sim.stability < 0.5)]
    if not c:
        return None
    r = max(c, key=lambda x: x["infection"])
    return {"rid": r["id"], "region": r["name"]}


def _panic_troops(sim, ctx):
    r = sim.regions[ctx["rid"]]
    _stab(sim, -0.03)
    r["lockdown"] = True
    r["dead"] = min(1.0, r["dead"] + 0.004)
    return "Order is restored in %s, at a price in blood." % ctx["region"]


def _panic_supplies(sim, ctx):
    _stab(sim, 0.05)
    return "Supplies calm the crowds in %s." % ctx["region"]


def _panic_ignore(sim, ctx):
    r = sim.regions[ctx["rid"]]
    _stab(sim, -0.08)
    r["infection"] = min(1.0, r["infection"] + 0.05)
    return "The crowds break the lines and the sickness spreads further."


# ------------------------------------------------------------------ 2. food
def _food_cond(sim):
    locks = sum(1 for r in _mine(sim) if r["lockdown"])
    if locks >= 2 or (sim.borders_closed_any and _closed_days(sim) >= 30):
        return {}
    return None


def _food_ration(sim, ctx):
    _stab(sim, -0.02)
    sim.mods["ration"] = sim.turn + 60
    return "Rationing starts. People grumble, but nobody starves yet."


def _food_import(sim, ctx):
    _stab(sim, 0.05)
    return "A convoy of grain keeps the cities calm."


def _food_open(sim, ctx):
    sim._set_channels(["sea", "land"], False)
    _stab(sim, 0.03)
    return "Roads and ports open for food. Every convoy is a risk."


# ------------------------------------------------------------------ 3. hospitals
def _hosp_cond(sim):
    return {} if sim._cinf.get(sim.player, 0) >= 0.12 else None


def _hosp_field(sim, ctx):
    sim.mods["hosp_a"] = sim.turn + 45
    return "Tents and stadiums turn into wards. Deaths fall for 45 days."


def _hosp_triage(sim, ctx):
    sim.mods["hosp_b"] = sim.turn + 30
    _stab(sim, -0.04)
    return "Doctors choose who gets a bed. It works, and nobody forgives it."


def _hosp_appeal(sim, ctx):
    _stab(sim, -0.01)
    donors = [c for c in sim.rel if sim.rel[c] > 15 and c not in sim.war and not sim.countries[c]["merged"]]
    sim.rng.shuffle(donors)
    got = 0
    for c in donors[:2]:
        sim.budget += 40
        sim._change_rel(c, 3)
        got += 1
    return "%d countries answer your appeal and send $%d." % (got, 40 * got) if got else "Nobody answers. Everyone is counting their own dead."


# ------------------------------------------------------------------ 4. coup
def _coup_cond(sim):
    if sim.stability < 0.4 and (sim.tech["military"] >= 1 or sim.war):
        return {}
    return None


def _coup_arrest(sim, ctx):
    _stab(sim, -0.04)
    sim.mods["army_weak"] = sim.turn + 60
    return "The generals are in cells. The army is weaker, and watching."


def _coup_buy(sim, ctx):
    _stab(sim, 0.03)
    return "Their loyalty is bought, for now."


def _coup_share(sim, ctx):
    _stab(sim, 0.05)
    sim.mods["junta"] = sim.turn + 60
    return "The generals take a cut of the state. Stability returns."


# ------------------------------------------------------------------ 5. black market
def _bm_cond(sim):
    return {} if sim.tech["medicine"] >= 3 and sim.med_stock >= 100 else None


def _bm_crack(sim, ctx):
    _stab(sim, -0.02)
    sim.budget += 60
    return "Raids seize the stock. $60 goes to the treasury."


def _bm_tax(sim, ctx):
    _stab(sim, -0.03)
    sim.budget += 100
    return "The black market now pays tax. Everyone knows where the money comes from."


def _bm_ignore(sim, ctx):
    sim.med_stock = max(0.0, sim.med_stock - 60)
    _stab(sim, -0.02)
    return "60 doses vanish from the warehouses."


# ------------------------------------------------------------------ 6. refugees
def _ref_cond(sim):
    n = _neighbours_in_trouble(sim, 0.2)
    if not n:
        return None
    cid = max(n, key=lambda c: sim._cinf.get(c, 0))
    return {"cid": cid, "country": sim._cname(cid)}


def _ref_open(sim, ctx):
    sim._change_rel(ctx["cid"], 10)
    healthy = [r for r in _mine(sim) if r["infection"] < 0.001 and not r["purged"]]
    if healthy and sim.rng.random() < 0.55:
        r = sim.rng.choice(healthy)
        r["infection"] = max(r["infection"], 0.004)
        return "The gates open. Among thousands, a few carry the sickness into %s." % r["name"]
    return "The gates open. Relations with %s warm. Nobody falls sick, this time." % ctx["country"]


def _ref_camp(sim, ctx):
    sim._change_rel(ctx["cid"], 4)
    return "A fenced camp takes them in. It holds."


def _ref_away(sim, ctx):
    sim._change_rel(ctx["cid"], -12)
    return "The border stays shut. %s will remember." % ctx["country"]


# ------------------------------------------------------------------ 7. shortcut
def _leak_cond(sim):
    return {} if sim.cure_started and sim.cure_progress >= 0.05 and sim.tech["research"] >= 1 else None


def _leak_fund(sim, ctx):
    sim.cure_progress = min(1.0, sim.cure_progress + 0.012)
    if sim.rng.random() < 0.35:
        _stab(sim, -0.05)
        return "The shortcut works (+1.2% cure), but the trial killed volunteers. It leaks."
    return "The shortcut works. The cure moves forward (+1.2%)."


def _leak_spy(sim, ctx):
    if sim.rng.random() < 0.5:
        sim.cure_progress = min(1.0, sim.cure_progress + 0.015)
        return "Your agents copy a rival's data (+1.5% cure)."
    cid = sim.rng.choice([c for c in sim.rel if c not in sim.war and not sim.countries[c]["merged"]])
    sim._change_rel(cid, -12)
    return "Your agents are caught in %s. Relations collapse." % sim._cname(cid)


def _leak_no(sim, ctx):
    return "You keep the books clean."


# ------------------------------------------------------------------ 8. cult
def _cult_cond(sim):
    return {} if (sim._world_dead_share >= 0.005 or sim.turn >= 120) and sim.turn >= 60 else None


def _cult_ban(sim, ctx):
    _stab(sim, -0.03)
    return "The Last Hour is banned. Its followers go underground."


def _cult_preach(sim, ctx):
    _stab(sim, -0.04)
    sim.budget += 80
    return "Preachers fill the squares. Donations pour in ($80). So does despair."


def _cult_recruit(sim, ctx):
    _stab(sim, 0.02)
    sim.mods["volunteers"] = sim.turn + 40
    return "The faithful nurse the sick. Fewer die for 40 days."


# ------------------------------------------------------------------ 9. deserters
def _des_cond(sim):
    c = [r for r in _mine(sim) if r["cordon"]]
    return {"rid": c[0]["id"], "region": c[0]["name"]} if c else None


def _des_shoot(sim, ctx):
    _stab(sim, -0.05)
    return "Deserters are shot. The cordon holds."


def _des_pay(sim, ctx):
    return "Double pay keeps the soldiers at their posts."


def _des_lift(sim, ctx):
    r = sim.regions[ctx["rid"]]
    r["cordon"] = False
    return "The cordon around %s is lifted. The soldiers go home." % ctx["region"]


# ------------------------------------------------------------------ 10. pharma offer
def _pharma_cond(sim):
    return {} if sim.tech["medicine"] >= 3 else None


def _pharma_exclusive(sim, ctx):
    sim.budget += 350
    sim.mods["pharma_lease"] = sim.turn + 120
    return "$350 in the bank. For four months a foreign firm owns a third of your output."


def _pharma_refuse(sim, ctx):
    return "You keep control of your medicine."


def _pharma_open(sim, ctx):
    sim.cure_progress = min(1.0, sim.cure_progress + 0.01) if sim.cure_started else sim.cure_progress
    for c in sim.rng.sample(sorted(sim.rel), 5):
        if c not in sim.war:
            sim._change_rel(c, 5)
    return "You share the formulas. The world cure speeds up and five countries think better of you."


# ------------------------------------------------------------------ 11. neighbour begs
def _beg_cond(sim):
    if sim.tech["medicine"] < 3 or sim.med_stock < 60:
        return None
    c = [cid for cid in sim.rel if sim._cinf.get(cid, 0) >= 0.3 and cid not in sim.war and not sim.countries[cid]["merged"]]
    if not c:
        return None
    cid = sim.rng.choice(c)
    return {"cid": cid, "country": sim._cname(cid)}


def _beg_send(sim, ctx):
    sim.med_stock -= 60
    sim.med_exported += 60
    sim._change_rel(ctx["cid"], 15)
    sim.countries[ctx["cid"]]["aid_until"] = sim.turn + 90
    return "60 doses reach %s. They will not forget it." % ctx["country"]


def _beg_sell(sim, ctx):
    sim.med_stock -= 60
    sim.med_exported += 60
    sim.budget += 100
    sim._change_rel(ctx["cid"], 3)
    sim.countries[ctx["cid"]]["aid_until"] = sim.turn + 90
    return "%s pays $100 for 60 doses." % ctx["country"]


def _beg_refuse(sim, ctx):
    sim._change_rel(ctx["cid"], -6)
    return "You turn down %s's plea." % ctx["country"]


# ------------------------------------------------------------------ 12. ceasefire offer
def _peace_cond(sim):
    c = [cid for cid in sim.war if sim.turn - sim.war_since.get(cid, sim.turn) >= 30]
    if not c:
        return None
    cid = sim.rng.choice(c)
    return {"cid": cid, "country": sim._cname(cid)}


def _peace_accept(sim, ctx):
    sim.war.discard(ctx["cid"])
    sim.war_since.pop(ctx["cid"], None)
    sim.truce[ctx["cid"]] = sim.turn + 60
    sim.rel[ctx["cid"]] = -30.0
    return "The guns fall silent for 60 days."


def _peace_refuse(sim, ctx):
    _stab(sim, 0.01)
    return "You refuse. The war goes on."


def _peace_demand(sim, ctx):
    if sim.rng.random() < 0.5:
        sim.budget += 150
        _peace_accept(sim, ctx)
        return "They pay $150 for peace. The guns fall silent for 60 days."
    sim.rel[ctx["cid"]] = min(sim.rel[ctx["cid"]], -90.0)
    return "They walk out of the talks. Hatred deepens."


# ------------------------------------------------------------------ 13. rebels
def _reb_cond(sim):
    c = [r for r in _mine(sim) if r["country_id"] != sim.player and r["country_id"] not in sim.union and r["unrest"] >= 0.5]
    if not c:
        return None
    r = max(c, key=lambda x: x["unrest"])
    return {"rid": r["id"], "region": r["name"], "home": sim._cname(r["country_id"]), "hid": r["country_id"]}


def _reb_crush(sim, ctx):
    r = sim.regions[ctx["rid"]]
    r["unrest"] = 0.0
    r["dead"] = min(1.0, r["dead"] + 0.02)
    _stab(sim, -0.03)
    sim._change_rel(ctx["hid"], -10)
    return "%s is crushed. The streets go quiet." % ctx["region"]


def _reb_auto(sim, ctx):
    sim.regions[ctx["rid"]]["unrest"] = max(0.0, sim.regions[ctx["rid"]]["unrest"] - 0.5)
    return "%s gets its own council. The anger cools." % ctx["region"]


def _reb_leave(sim, ctx):
    r = sim.regions[ctx["rid"]]
    r["owner"] = r["country_id"]
    r["unrest"], r["lockdown"], r["cordon"] = 0.0, False, False
    sim.conquests = max(0, sim.conquests - 1)
    sim._change_rel(ctx["hid"], 25)
    return "You withdraw from %s. %s owes you one." % (ctx["region"], ctx["home"])


# ------------------------------------------------------------------ 14. sabotage
def _sab_cond(sim):
    return {} if sim.cure_started and sim.cure_progress >= 0.1 else None


def _sab_guard(sim, ctx):
    sim.cure_progress = max(0.0, sim.cure_progress - 0.005)
    return "Your guards save most of the work. The cure slips back only 0.5%."


def _sab_none(sim, ctx):
    sim.cure_progress = max(0.0, sim.cure_progress - 0.015)
    return "A laboratory burns. The cure slips back 1.5%."


# ------------------------------------------------------------------ 15. exodus
def _exo_cond(sim):
    c = [r for r in _mine(sim) if r["dead"] >= 0.25 and not r["purged"]]
    if not c:
        return None
    r = max(c, key=lambda x: x["dead"])
    return {"rid": r["id"], "region": r["name"]}


def _exo_relocate(sim, ctx):
    _stab(sim, 0.02)
    return "Buses and trains move the survivors out of %s. They are counted and checked." % ctx["region"]


def _exo_close(sim, ctx):
    _stab(sim, -0.03)
    return "Roads out of %s are shut. The survivors are left to their fate." % ctx["region"]


def _exo_let(sim, ctx):
    healthy = [r for r in _mine(sim) if r["infection"] < 0.001 and not r["purged"]]
    if healthy:
        r = sim.rng.choice(healthy)
        r["infection"] = max(r["infection"], 0.004)
        return "The survivors walk where they like. The sickness walks with them, into %s." % r["name"]
    return "The survivors scatter. This time nothing follows them."


# ------------------------------------------------------------------ 16. protest
def _prot_cond(sim):
    return {} if sim.borders_closed_any and _closed_days(sim) >= 40 and sim.stability < 0.5 else None


def _prot_open(sim, ctx):
    sim._set_channels(["air", "sea", "land"], False)
    _stab(sim, 0.08)
    return "Borders reopen to cheering crowds. The world is back at your door."


def _prot_ban(sim, ctx):
    _stab(sim, -0.05)
    return "Protests are banned. The squares fill with soldiers."


def _prot_vote(sim, ctx):
    if sim.rng.random() < 0.5:
        _stab(sim, 0.05)
        return "The referendum backs the closed borders. People feel heard."
    _stab(sim, -0.02)
    return "The referendum splits the country in two."


# ------------------------------------------------------------------ 17. rumour
def _rum_cond(sim):
    return {} if sim.tech["medicine"] >= 2 and sim.turn >= 80 else None


def _rum_campaign(sim, ctx):
    _stab(sim, 0.03)
    return "A loud public campaign wins the argument."


def _rum_censor(sim, ctx):
    _stab(sim, -0.03)
    return "The rumours are banned, which makes everyone repeat them."


# ------------------------------------------------------------------ 18. drifting ship
def _ship_cond(sim):
    c = sim.countries[sim.player]
    if not c["sea_open"] or sim._world_share < 0.02 or not c["has_port"]:
        return None
    return {}


def _ship_sink(sim, ctx):
    _stab(sim, -0.02)
    return "The ship goes down with everyone aboard. Nobody speaks of it."


def _ship_tow(sim, ctx):
    return "The ship is towed to a quarantine anchorage. Divers find the dead."


def _ship_dock(sim, ctx):
    r = [x for x in _mine(sim) if x["infection"] < 0.001 and not x["purged"]]
    if r and sim.rng.random() < 0.45:
        t = sim.rng.choice(r)
        t["infection"] = max(t["infection"], 0.004)
        return "The ship docks. Two days later the first cases appear in %s." % t["name"]
    return "The ship docks and the sick are treated. Luck holds."


# ------------------------------------------------------------------ early game
def _alarm_cond(sim):
    return {} if 8 <= sim.turn <= 90 and not sim.borders_closed_any and sim._world_share < 0.05 else None


def _alarm_ground(sim, ctx):
    sim._set_channels(["air"], True)
    _stab(sim, -0.02)
    return "Every flight in and out is cancelled. Airlines are furious."


def _alarm_screen(sim, ctx):
    sim.mods["screening"] = sim.turn + 90
    return "Thermal cameras at every airport. Fewer infected arrivals for 90 days."


def _alarm_wait(sim, ctx):
    return "You decide to wait for more data."


def _buy_cond(sim):
    return {} if 12 <= sim.turn <= 140 else None


def _buy_ration(sim, ctx):
    _stab(sim, 0.03)
    return "Limits per customer keep shelves stocked."


def _buy_speech(sim, ctx):
    _stab(sim, 0.01)
    return "Your address is calm and clear. It helps a little."


def _buy_ignore(sim, ctx):
    _stab(sim, -0.03)
    return "Supermarkets are emptied by evening."


def _donor_cond(sim):
    return {} if 20 <= sim.turn <= 240 else None


def _donor_take(sim, ctx):
    sim.budget += 150
    _stab(sim, -0.02)
    return "$150 arrives. So do the questions about what he wants in return."


def _donor_open(sim, ctx):
    sim.budget += 100
    return "He gives $100, publicly, with no strings. The press loves it."


def _donor_no(sim, ctx):
    _stab(sim, 0.01)
    return "You turn down the money. Voters notice."


def _stud_cond(sim):
    return {} if 25 <= sim.turn <= 260 else None


def _stud_train(sim, ctx):
    sim.mods["volunteers"] = sim.turn + 40
    return "Two thousand trained students staff the hospitals. Deaths fall for 40 days."


def _stud_courier(sim, ctx):
    _stab(sim, 0.02)
    return "The students deliver food to the locked-down streets. People remember."


def _stud_home(sim, ctx):
    _stab(sim, -0.01)
    return "You send the students home. Some are angry."


def _press_cond(sim):
    return {} if 15 <= sim.turn <= 300 else None


def _press_truth(sim, ctx):
    _stab(sim, -0.01)
    for c in sim.rng.sample(sorted(sim.rel), 5):
        if c not in sim.war:
            sim._change_rel(c, 3)
    return "You tell the truth. Five foreign ministries take note."


def _press_downplay(sim, ctx):
    _stab(sim, 0.03)
    return "You promise there is nothing to fear. For now, people believe you."


def _press_silence(sim, ctx):
    return "You say nothing. The rumours say it for you."



EVENTS = [
    dict(id="alarm", title="The first alarm", mood="shield", cd=200, weight=1.8, cond=_alarm_cond,
         text="Your health minister puts three reports on the table. Every one of them says the same thing, in different words: it is already out, and it travels by plane.",
         choices=[dict(label="Ground all flights", hint="Air borders closed. Stability -2%.", fn=_alarm_ground),
                  dict(label="Screen every arrival", hint="Infected arrivals -30% for 90 days.", cost=50, fn=_alarm_screen),
                  dict(label="Wait for more data", hint="Nothing changes.", fn=_alarm_wait)]),
    dict(id="buying", title="Panic buying", mood="fire", cd=200, weight=1.4, cond=_buy_cond,
         text="By lunch the pasta aisles are empty. A man has been arrested with four trolleys of toilet paper, and the news is broadcasting it live.",
         choices=[dict(label="Ration essentials", hint="Stability +3%.", cost=40, fn=_buy_ration),
                  dict(label="Make a calm address", hint="Stability +1%.", fn=_buy_speech),
                  dict(label="Do nothing", hint="Stability -3%.", fn=_buy_ignore)]),
    dict(id="donor", title="A very generous man", mood="crown", cd=240, weight=1.0, cond=_donor_cond,
         text="A billionaire wants to fund your response. His company, you notice, sells ventilators.",
         choices=[dict(label="Take the money quietly", hint="+$150. Stability -2%.", fn=_donor_take),
                  dict(label="Take it, in public, no strings", hint="+$100.", fn=_donor_open),
                  dict(label="Decline", hint="Stability +1%.", fn=_donor_no)]),
    dict(id="students", title="The students volunteer", mood="shield", cd=250, weight=1.0, cond=_stud_cond,
         text="Medical students are queuing outside the ministry. Two thousand of them, with masks they made themselves.",
         choices=[dict(label="Train them for the wards", hint="Deaths -10% for 40 days.", cost=60, fn=_stud_train),
                  dict(label="Use them as couriers", hint="Stability +2%.", fn=_stud_courier),
                  dict(label="Send them home", hint="Stability -1%.", fn=_stud_home)]),
    dict(id="press", title="The press room", mood="crown", cd=250, weight=1.0, cond=_press_cond,
         text="Two hundred journalists, one question: how bad is it? You can tell them exactly, or you can tell them what they want to hear.",
         choices=[dict(label="Tell the truth", hint="Stability -1%. Relations +3 with five countries.", fn=_press_truth),
                  dict(label="Downplay it", hint="Stability +3%.", fn=_press_downplay),
                  dict(label="Say nothing", hint="Nothing changes.", fn=_press_silence)]),
    dict(id="panic", title="Panic in {region}", mood="fire", cd=60, weight=1.6, cond=_panic_cond,
         text="Crowds are smashing shop windows in {region}. The lockdown has gone on too long and people are afraid of everything: the disease, the soldiers, each other.",
         choices=[dict(label="Send in the troops", hint="Stability -3%. A few die. Region stays locked.", fn=_panic_troops),
                  dict(label="Hand out supplies", hint="Stability +5%.", cost=40, fn=_panic_supplies),
                  dict(label="Let it burn", hint="Stability -8%. Infection rises.", fn=_panic_ignore)]),
    dict(id="food", title="The shelves are empty", mood="fire", cd=90, weight=1.0, cond=_food_cond,
         text="Closed roads and sealed regions are starving the cities. Bakers have no flour and queues wrap around the blocks.",
         choices=[dict(label="Ration food", hint="Stability -2%. Income -10% for 60 days.", fn=_food_ration),
                  dict(label="Emergency imports", hint="Stability +5%.", cost=90, fn=_food_import),
                  dict(label="Open ports and roads for convoys", hint="Stability +3%. Sea and land borders open.", fn=_food_open)]),
    dict(id="hospitals", title="The hospitals are full", mood="shield", cd=80, weight=1.4, cond=_hosp_cond,
         text="Corridors are lined with stretchers. Doctors are working their third day without sleep and a nurse is asking who gets the last ventilator.",
         choices=[dict(label="Build field hospitals", hint="Deaths -30% for 45 days.", cost=100, fn=_hosp_field),
                  dict(label="Triage by survival odds", hint="Deaths -15% for 30 days. Stability -4%.", fn=_hosp_triage),
                  dict(label="Appeal to the world", hint="Maybe $80 from friendly countries. Stability -1%.", fn=_hosp_appeal)]),
    dict(id="coup", title="Whispers in the barracks", mood="crown", cd=120, weight=1.0, cond=_coup_cond,
         text="Three generals meet after midnight. Your intelligence chief says they are not talking about the disease.",
         choices=[dict(label="Arrest the generals", hint="Stability -4%. Army -20% for 60 days.", fn=_coup_arrest),
                  dict(label="Buy their loyalty", hint="Stability +3%.", cost=120, fn=_coup_buy),
                  dict(label="Give them a share of power", hint="Stability +5%. Income -10% for 60 days.", fn=_coup_share)]),
    dict(id="blackmarket", title="Dealers in the dark", mood="flask", cd=120, weight=1.0, cond=_bm_cond,
         text="Your vaccines are being sold from car boots at ten times the price. Everyone knows someone who got a dose that way.",
         choices=[dict(label="Crack down", hint="$60 seized. Stability -2%.", fn=_bm_crack),
                  dict(label="Tax it", hint="+$100. Stability -3%.", fn=_bm_tax),
                  dict(label="Look away", hint="Lose 60 doses. Stability -2%.", fn=_bm_ignore)]),
    dict(id="refugees", title="Refugees at the gate", mood="wave", cd=100, weight=1.2, cond=_ref_cond,
         text="Thousands from {country} are on your border, carrying what they can. Some are coughing. Your border guards are asking for orders.",
         choices=[dict(label="Open the gates", hint="Relations +10. Risk of a new infection.", fn=_ref_open),
                  dict(label="Build a quarantine camp", hint="Relations +4. Safe.", cost=60, fn=_ref_camp),
                  dict(label="Turn them away", hint="Relations -12 with {country}.", fn=_ref_away)]),
    dict(id="shortcut", title="A shortcut to the cure", mood="flask", cd=150, weight=0.9, cond=_leak_cond,
         text="A researcher offers a way to skip two years of trials. It means testing on people who did not agree to it. Or you could buy the data from someone who already did.",
         choices=[dict(label="Fund the trial", hint="Cure +1.2%. 35% chance of a scandal (stability -5%).", cost=150, fn=_leak_fund),
                  dict(label="Send agents to steal it", hint="Cure +1.5%, or a diplomatic disaster.", cost=80, fn=_leak_spy),
                  dict(label="Decline", hint="Nothing happens.", fn=_leak_no)]),
    dict(id="cult", title="The Last Hour", mood="skull", cd=150, weight=0.9, cond=_cult_cond,
         text="They gather in squares at dusk and say the end was promised. They are gaining a thousand followers a day.",
         choices=[dict(label="Ban the cult", hint="Stability -3%.", fn=_cult_ban),
                  dict(label="Let them preach", hint="+$80. Stability -4%.", fn=_cult_preach),
                  dict(label="Make them volunteers", hint="Stability +2%. Deaths -10% for 40 days.", fn=_cult_recruit)]),
    dict(id="deserters", title="Soldiers refuse to fire", mood="skull", cd=60, weight=1.5, cond=_des_cond,
         text="Some of the men guarding the cordon around {region} have put down their rifles. They say they will not shoot their neighbours.",
         choices=[dict(label="Shoot the deserters", hint="Stability -5%. The cordon holds.", fn=_des_shoot),
                  dict(label="Double their pay", hint="The cordon holds.", cost=80, fn=_des_pay),
                  dict(label="Lift the cordon", hint="Region stays locked down, no more shooting.", fn=_des_lift)]),
    dict(id="pharma", title="An offer from abroad", mood="flask", cd=150, weight=0.8, cond=_pharma_cond,
         text="A foreign pharmaceutical group wants exclusive rights to your medicine. They are polite. They are offering a lot of money.",
         choices=[dict(label="Sell exclusive rights", hint="+$350. Medicine output -30% for 120 days.", fn=_pharma_exclusive),
                  dict(label="Refuse", hint="Nothing changes.", fn=_pharma_refuse),
                  dict(label="Share the formulas", hint="Cure +1%. Relations +5 with five countries.", fn=_pharma_open)]),
    dict(id="beg", title="{country} begs for medicine", mood="wave", cd=100, weight=1.2, cond=_beg_cond,
         text="Their health minister is on the line. Hospitals are overrun and they have heard you have doses.",
         choices=[dict(label="Send 60 doses as aid", hint="Relations +15. They get protection for 90 days.", fn=_beg_send),
                  dict(label="Sell 60 doses", hint="+$100. Relations +3.", fn=_beg_sell),
                  dict(label="Refuse", hint="Relations -6.", fn=_beg_refuse)]),
    dict(id="peace", title="{country} asks for peace", mood="crown", cd=80, weight=1.6, cond=_peace_cond,
         text="After weeks of war, an envoy from {country} crosses the line under a white flag.",
         choices=[dict(label="Accept a ceasefire", hint="War ends. 60 days of truce.", fn=_peace_accept),
                  dict(label="Demand tribute", hint="50%: +$150 and peace. 50%: talks collapse.", fn=_peace_demand),
                  dict(label="Refuse", hint="The war goes on. Stability +1%.", fn=_peace_refuse)]),
    dict(id="rebels", title="{region} is rising", mood="fire", cd=60, weight=1.8, cond=_reb_cond,
         text="Flags of {home} hang from the windows of {region}. Your garrison reports sniper fire at night.",
         choices=[dict(label="Crush the revolt", hint="Unrest to 0. Stability -3%. Relations -10 with {home}.", fn=_reb_crush),
                  dict(label="Grant autonomy", hint="Unrest -50%.", fn=_reb_auto),
                  dict(label="Withdraw", hint="Give the region back. Relations +25 with {home}.", fn=_reb_leave)]),
    dict(id="sabotage", title="A laboratory burns", mood="fire", cd=120, weight=0.8, cond=_sab_cond,
         text="Someone set fire to a cure laboratory overnight. Security cameras show three people leaving by the back.",
         choices=[dict(label="Send your guards", hint="Cure only -0.5%.", cost=100, fn=_sab_guard),
                  dict(label="Do nothing", hint="Cure -1.5%.", fn=_sab_none)]),
    dict(id="exodus", title="The survivors of {region}", mood="wave", cd=100, weight=1.4, cond=_exo_cond,
         text="A quarter of {region} is dead. The rest are packing whatever they can and heading down the roads.",
         choices=[dict(label="Organise their relocation", hint="Stability +2%.", cost=60, fn=_exo_relocate),
                  dict(label="Close the roads", hint="Stability -3%. No spread.", fn=_exo_close),
                  dict(label="Let them go", hint="Risk of spreading the infection.", fn=_exo_let)]),
    dict(id="protest", title="Open the borders!", mood="crown", cd=100, weight=1.3, cond=_prot_cond,
         text="Students, drivers and shopkeepers are in the streets. Weeks of closed borders have cost them their jobs. They want out of the cage.",
         choices=[dict(label="Open the borders", hint="Stability +8%. All borders open.", fn=_prot_open),
                  dict(label="Ban the protests", hint="Stability -5%.", fn=_prot_ban),
                  dict(label="Hold a referendum", hint="50%: stability +5%. 50%: stability -2%.", cost=40, fn=_prot_vote)]),
    dict(id="rumour", title="The vaccine lie", mood="skull", cd=120, weight=0.8, cond=_rum_cond,
         text="A viral video says the medicine is poison. Half the country has stopped queueing.",
         choices=[dict(label="Public campaign", hint="Stability +3%.", cost=50, fn=_rum_campaign),
                  dict(label="Censor it", hint="Stability -3%.", fn=_rum_censor)]),
    dict(id="ship", title="A ship without a crew", mood="wave", cd=120, weight=0.9, cond=_ship_cond,
         text="A cargo ship is drifting towards your coast. Half the crew are on deck, the rest are not moving.",
         choices=[dict(label="Sink it", hint="Stability -2%. Safe.", fn=_ship_sink),
                  dict(label="Tow it to quarantine", hint="Safe.", cost=50, fn=_ship_tow),
                  dict(label="Let it dock", hint="45% chance of an infection.", fn=_ship_dock)]),
]
BY_ID = {e["id"]: e for e in EVENTS}


# ------------------------------------------------------------------ engine
def eligible(sim):
    """[(weight, event, ctx)] for events that can fire right now."""
    out = []
    for ev in EVENTS:
        if sim.turn < sim.event_cd.get(ev["id"], 0):
            continue
        ctx = ev["cond"](sim)
        if ctx is not None:
            out.append((ev["weight"], ev, ctx))
    return out


def _fmt(s, ctx):
    try:
        return s.format(**ctx)
    except (KeyError, IndexError):
        return s


def build(sim, ev, ctx):
    choices = []
    for i, ch in enumerate(ev["choices"]):
        cost = ch.get("cost", 0)
        choices.append({"i": i, "label": _fmt(ch["label"], ctx), "hint": _fmt(ch.get("hint", ""), ctx), "cost": cost})
    return {"kind": "event", "id": ev["id"], "title": _fmt(ev["title"], ctx), "text": _fmt(ev["text"], ctx),
            "mood": ev["mood"], "choices": choices, "_ctx": ctx, "_ev": ev}


def card(key, title, text, mood="skull"):
    return {"kind": "card", "id": key, "title": title, "text": text, "mood": mood,
            "choices": [{"i": 0, "label": "Continue", "hint": "", "cost": 0}], "_ctx": {}, "_ev": None}


def resolve(sim, pending, index):
    """Apply a choice. Returns (ok, message)."""
    if index < 0 or index >= len(pending["choices"]):
        return False, "Pick one of the options."
    if pending["kind"] == "card":
        return True, ""
    ch = pending["_ev"]["choices"][index]
    cost = ch.get("cost", 0)
    if sim.budget < cost:
        return False, "You need $%d for that." % cost
    sim.budget -= cost
    msg = ch["fn"](sim, pending["_ctx"])
    sim.event_cd[pending["_ev"]["id"]] = sim.turn + pending["_ev"]["cd"]
    return True, msg