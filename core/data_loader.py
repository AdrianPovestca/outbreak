import json
import os


def load_json(data_dir: str, filename: str):
    path = os.path.join(data_dir, filename)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_all(data_dir: str = "data"):
    return {
        "countries": load_json(data_dir, "countries.json"),
        "regions": load_json(data_dir, "regions.json"),
        "infection_types": load_json(data_dir, "infection_types.json"),
    }
