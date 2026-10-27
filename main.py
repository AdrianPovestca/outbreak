from core.simulation import Simulation


if __name__ == "__main__":
    sim = Simulation(
        disease_choice=None,     # None = random, or "bacteria" / "virus" / "zombie"
        difficulty="medium",
        player_country="RO",
    )
    print("Disease:", sim.disease["id"])
    sim.run(20)
    print(sim.summary())
    for line in sim.log[-10:]:
        print(line)
