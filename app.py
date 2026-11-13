"""
Outbreak - Flask backend.

Run:   pip install flask
       python app.py          ->  http://127.0.0.1:5000
"""
import re
from threading import Lock

from flask import Flask, jsonify, render_template, request, send_from_directory

from core.simulation import DEFAULT_DATA_DIR, Simulation, ensure_world, list_countries

app = Flask(__name__, template_folder="templates")

GAME = None          # single-player: one game kept in memory
LOCK = Lock()

ensure_world()       # make sure data/regions.json exists before the first request


def _error(message, status=400):
    return jsonify({"error": message, "ok": False}), status


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
    match = next((c for c in list_countries() if c["id"] == wanted or c["iso3"] == wanted), None)
    if not match:
        return _error("Unknown country: %s" % wanted)
    if disease not in ("random", "bacteria", "virus", "zombie"):
        return _error("Unknown disease: %s" % disease)
    with LOCK:
        GAME = Simulation(player_country=match["id"], disease=disease)   # starts on today's date
        return jsonify(GAME.summary(full=True))


@app.route("/api/state", methods=["GET"])
def api_state():
    with LOCK:
        if GAME is None:
            return jsonify({"started": False, "countries": list_countries()})
        GAME._refresh_cache()
        return jsonify(GAME.summary(full=True))


@app.route("/api/action", methods=["POST"])
def api_action():
    """tick (days), close_borders, open_borders, upgrade (track), invade, lockdown, evacuate, cordon, purge,
    medicine_policy, improve, trade, alliance, union, ceasefire, sell_medicine"""
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