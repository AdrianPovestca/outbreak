from core.simulation import Simulation


def test_loads():
    sim = Simulation(disease_choice="virus", difficulty="easy", player_country="RO")
    assert sim.disease["id"] == "virus"
    assert len(sim.regions) > 0
    assert len(sim.countries) > 0


def test_tick_runs():
    sim = Simulation(disease_choice="bacteria", difficulty="medium", player_country="RO")
    sim.run(5)
    assert sim.turn == 5


def test_retreat_trigger():
    sim = Simulation(disease_choice="zombie", difficulty="hard", player_country="RO")
    sim.regions["RO_DOBROGEA"]["infection"]["zombie"] = 0.8
    sim._phase_retreat()
    assert sim.regions["RO_DOBROGEA"]["control"] in ("abandoned", "infected")


def test_cure_race():
    sim = Simulation(disease_choice="virus", difficulty="easy", player_country="DE")
    for c in sim.countries.values():
        c["cure_progress"] = 0.99
    sim._phase_cure()
    assert sim.cure_owner is not None
