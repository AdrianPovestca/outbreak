"""
Outbreak - Flask backend.

Run:   pip install flask
       python app.py          ->  http://127.0.0.1:5000

The game saves itself to saves/autosave.pkl as you play (the file is only ever read by this
program on your own machine). Use "Continue" in the start menu to pick it up again.
"""
import json
import os
import re
from threading import Lock

from flask import Flask, jsonify, render_template, request, send_from_directory

from core.simulation import DEFAULT_DATA_DIR, DIFFICULTY, Simulation, ensure_world, list_countries

app = Flask(__name__, template_folder="templates")

GAME = None          # single-player: one game kept in memory
LOCK = Lock()
SAVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "saves")
SAVE_PATH = os.path.join(SAVE_DIR, "autosave.pkl")
META_PATH = os.path.join(SAVE_DIR, "autosave.json")

ensure_world()       # make sure data/regions.json exists before the first request


def _error(message, status=400):
    return jsonify({"error": message, "ok": False}), status


def _save():
    try:
        GAME.save(SAVE_PATH)
        with open(META_PATH, "w", encoding="utf-8") as f:
            json.dump(GAME.meta(), f)
    except Exception as e:                                   # a failed save must never break the game
        print("Autosave failed:", e)


def _clear_save():
    for p in (SAVE_PATH, META_PATH):
        try:
            os.remove(p)
        except OSError:
            pass


def _save_meta():
    try:
        with open(META_PATH, "r", encoding="utf-8") as f:
            return json.load(f) if os.path.exists(SAVE_PATH) else None
    except (OSError, ValueError):
        return None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/data/<path:name>")
def data_file(name):
    """Serves data/world.geo.json (offline fallback for the map)."""
    return send_from_directory(DEFAULT_DATA_DIR, name)


@app.route("/api/start", methods=["POST"])
def api_start():
    global GAME
    body = request.get_json(silent=True) or {}
    wanted = str(body.get("country", "")).upper()
    disease = str(body.get("disease", "random")).lower()
    difficulty = str(body.get("difficulty", "normal")).lower()
    match = next((c for c in list_countries() if c["id"] == wanted or c["iso3"] == wanted), None)
    if not match:
        return _error("Unknown country: %s" % wanted)
    if disease not in ("random", "bacteria", "virus", "zombie"):
        return _error("Unknown disease: %s" % disease)
    if difficulty not in DIFFICULTY:
        return _error("Unknown difficulty: %s" % difficulty)
    with LOCK:
        GAME = Simulation(player_country=match["id"], disease=disease, difficulty=difficulty)   # starts on today's date
        _save()
        return jsonify(GAME.summary(full=True))


@app.route("/api/continue", methods=["POST"])
def api_continue():
    global GAME
    with LOCK:
        if not os.path.exists(SAVE_PATH):
            return _error("There is no saved game.")
        try:
            GAME = Simulation.load(SAVE_PATH)
        except Exception as e:
            return _error("The saved game cannot be loaded: %s" % e)
        GAME._refresh_cache()
        return jsonify(GAME.summary(full=True))


@app.route("/api/state", methods=["GET"])
def api_state():
    with LOCK:
        if GAME is None:
            return jsonify({"started": False, "countries": list_countries(), "save": _save_meta(),
                            "difficulties": {k: {"label": v["label"], "text": v["text"]} for k, v in DIFFICULTY.items()}})
        GAME._refresh_cache()
        return jsonify(GAME.summary(full=True))


@app.route("/api/action", methods=["POST"])
def api_action():
    """tick (days), close_borders / open_borders (mode), upgrade (node), sell_node, invade (force), lockdown, evacuate,
    cordon, purge, medicine_policy, choose, improve, trade, alliance, union, ceasefire, sell_medicine"""
    body = request.get_json(silent=True) or {}
    action = str(body.get("action", ""))
    with LOCK:
        if GAME is None:
            return _error("No game running. Call /api/start first.")
        if not re.fullmatch(r"[a-z_]+", action):
            return _error("Unknown action: %s" % action)
        ok, message = GAME.do_action(action, body)
        if not ok:
            return _error(message)
        if GAME.game_over:
            _clear_save()
        elif action != "tick" or GAME.turn % 4 == 0 or GAME._pending:
            _save()
        data = GAME.summary(full=False)
        data.update(ok=True, message=message)
        return jsonify(data)


@app.route("/api/region/<rid>", methods=["GET"])
def api_region(rid):
    with LOCK:
        if GAME is None:
            return _error("No game running.")
        info = GAME.region_info(rid)
        return jsonify(info) if info else _error("Unknown region.", 404)


@app.route("/api/diplomacy", methods=["GET"])
def api_diplomacy():
    with LOCK:
        if GAME is None:
            return _error("No game running.")
        return jsonify({"countries": GAME.diplomacy()})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)