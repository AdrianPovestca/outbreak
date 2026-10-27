from core.simulation import Simulation
import os

def clear():
    os.system('cls' if os.name == 'nt' else 'clear')

if __name__ == "__main__":
    print("=== OUTBREAK ===")
    print("Choose disease: 1. Bacteria  2. Virus  3. Zombie  4. Random")
    d_choice = input("Choice (1-4): ")
    disease = None
    if d_choice == "1": disease = "bacteria"
    elif d_choice == "2": disease = "virus"
    elif d_choice == "3": disease = "zombie"

    print("\nChoose difficulty: 1. Easy  2. Medium  3. Hard  4. Extra Hard")
    diff_choice = input("Choice (1-4): ")
    diff = "medium"
    if diff_choice == "1": diff = "easy"
    elif diff_choice == "3": diff = "hard"
    elif diff_choice == "4": diff = "extra_hard"

    sim = Simulation(disease_choice=disease, difficulty=diff, player_country="RO")
    
    clear()
    print(f"Starting as {sim.player_country}. Disease: {sim.disease['id'].upper()}.")

    while not sim.game_over:
        s = sim.summary()
        print(f"\n--- TURN {s['turn']} ---")
        print(f"Disease: {s['disease']} | Avg Infection: {s['avg_infection']*100:.1f}%")
        print(f"Your Territory: {', '.join(s['player_territory'])}")
        print(f"Black Zone Regions: {s['black_zone_regions']}")
        print(f"Cure Owner: {s['cure_owner']}")
        
        print("\nACTIONS:")
        print("1. Wait / Next Turn")
        print("2. Close Borders")
        print("3. Open Borders")
        print("4. Invade Region (Military)")
        print("5. Propose Alliance")
        print("Q. Quit")
        
        action = input("> ").upper()
        
        if action == "Q": break
        elif action == "1": sim.tick()
        elif action == "2": sim.player_close_borders()
        elif action == "3": sim.player_open_borders()
        elif action == "4":
            r_id = input("Enter Region ID to invade (e.g. MD_CHISINAU, RO_DOBROGEA): ").upper()
            res = sim.player_occupy_region(r_id)
            print(res)
        elif action == "5":
            c_id = input("Enter Country ID to ally (e.g. MD, BG): ").upper()
            res = sim.player_propose_alliance(c_id)
            print(res)
            
        if len(sim.log) > 0:
            print("\nRecent Events:")
            for line in sim.log[-3:]:
                print(line)
            sim.log = [] # clear log for next UI update

    print("\nGAME OVER!")
    print(f"Result: {sim.result}")
