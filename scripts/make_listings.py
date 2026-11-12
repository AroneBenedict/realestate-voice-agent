"""Generate the fictional property catalogue in data/projects.json.

    python scripts/make_listings.py

Every project, developer and registration number here is invented. Prices follow a simple
per-square-foot rate for each locality so budgets and listings stay internally consistent.
"""

import json
import random
from pathlib import Path

LOCALITIES = {  # locality -> (zone, price per sq ft in rupees)
    "Whitefield": ("East", 8500),
    "KR Puram": ("East", 7000),
    "Sarjapur Road": ("South-East", 8000),
    "HSR Layout": ("South-East", 12000),
    "Electronic City": ("South", 6000),
    "Bannerghatta Road": ("South", 8000),
    "Kanakapura Road": ("South", 7000),
    "Hebbal": ("North", 10000),
    "Hennur": ("North", 7500),
    "Yelahanka": ("North", 7000),
    "Devanahalli": ("North", 5500),
}
SIZES = {1: 650, 2: 1150, 3: 1650, 4: 2400}
FIRST = ["Aster", "Banyan", "Cedar", "Dewdrop", "Emerald", "Fernwood", "Grove", "Harbor", "Ivory", "Juniper", "Kestrel",
         "Lotus", "Maple", "Nimbus", "Orchid", "Pinecrest", "Quartz", "Riverstone", "Saffron", "Teakwood", "Willow", "Zenith"]
SECOND = ["Greens", "Heights", "Residency", "Meadows", "Enclave", "Gardens", "Towers", "Courtyard", "Vista", "Park"]
DEVELOPERS = ["Sample Homes", "Demo Estates", "Example Builders", "Placeholder Realty"]
POSSESSION = ["Ready to move", "Ready to move", "June 2027", "December 2027", "March 2028", "December 2028"]
AMENITIES = ["swimming pool", "clubhouse", "gym", "children's play area", "jogging track", "indoor games room",
             "co-working space", "badminton court", "landscaped garden", "power backup"]


def main() -> None:
    rng = random.Random(2026)
    names = [f"{a} {b}" for a in FIRST for b in SECOND]
    rng.shuffle(names)
    projects = []
    for locality, (zone, rate) in LOCALITIES.items():
        for _ in range(2):
            pid = f"P{len(projects) + 1:02d}"
            bhks = sorted(rng.sample([1, 2, 3, 4], rng.choice([2, 3])))
            configs = []
            local_rate = rate * rng.uniform(0.9, 1.12)
            for bhk in bhks:
                low = SIZES[bhk] * rng.uniform(0.92, 1.0)
                high = low * rng.uniform(1.06, 1.15)
                configs.append({
                    "bhk": bhk,
                    "sqft_min": int(round(low, -1)),
                    "sqft_max": int(round(high, -1)),
                    "price_min_lakh": round(low * local_rate / 1e5, 1),
                    "price_max_lakh": round(high * local_rate / 1e5, 1),
                })
            facts = {
                "possession": rng.choice(POSSESSION),
                "rera": f"DEMO-RERA-{100 + len(projects)}",
                "amenities": ", ".join(rng.sample(AMENITIES, 5)),
                "maintenance": f"{rng.choice([3, 3.5, 4, 4.5, 5])} rupees per square foot per month",
                "parking": rng.choice(["one covered car park per unit", "one covered car park, two for 3 BHK and above"]),
                "metro": f"{round(rng.uniform(0.6, 6.0), 1)} km to the nearest metro station",
                "payment_plan": rng.choice(["construction-linked payment plan",
                                            "10 percent on booking, rest linked to construction",
                                            "full payment on registration (ready unit)"]),
                "home_loan": "home loans available through partner banks, subject to the bank's approval",
            }
            if rng.random() < 0.5:  # only some brochures mention pets; the agent must not guess for the rest
                facts["pets"] = rng.choice(["pet friendly, with a pet park", "pets allowed as per association rules"])
            if facts["possession"] == "Ready to move":
                facts["payment_plan"] = "full payment on registration (ready unit)"
            projects.append({
                "id": pid,
                "name": names.pop(),
                "developer": rng.choice(DEVELOPERS),
                "locality": locality,
                "zone": zone,
                "units_available": rng.randint(4, 60),
                "configs": configs,
                "facts": facts,
            })
    out = {
        "note": "Fictional catalogue for testing. No project, developer or registration number is real.",
        "localities": {name: {"zone": zone} for name, (zone, _) in LOCALITIES.items()},
        "projects": projects,
    }
    path = Path(__file__).resolve().parents[1] / "data" / "projects.json"
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(projects)} projects to {path}")


if __name__ == "__main__":
    main()
