"""Generate a synthetic local catalog (data/exports/catalog.json) for offline
verification — no Supabase needed. Values are drawn from the real taxonomy so
the per-feature MDS blocks actually get exercised."""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.config import REPO_ROOT  # noqa: E402

random.seed(7)

STYLES = ["Casual", "Streetwear", "Grunge", "Y2K Fashion", "Formal", "Minimalist", "Gothic", "Preppy"]
SUB = ["y2k", "grunge", "minimalist", "vintage", "hypebeast", "techwear", "gothic"]
DESIGN = ["solid", "graphic_printed", "striped", "tie_dye", "checkered"]
CATS_TOP = ["T-Shirts", "Shirts", "Hoodies", "Sweatshirts"]
CATS_BOTTOM = ["Jeans", "Cargos", "Joggers", "Shorts"]
COLORS = ["#101010", "#F5F5F5", "#2D5AB4", "#8B1A23", "#3C8C46"]
SLEEVES = ["Full Sleeves", "Half Sleeves", "Sleeveless"]
NECKS = ["Round Neck", "Polo", "V Neck", "Hooded"]
TOPFIT = ["Oversized", "Slim Fit", "Relaxed Fit", "Boxy Fit"]
BOTFIT = ["Slim Fit", "Relaxed Fit", "Baggy Fit", "Straight Fit"]
WAIST = ["Mid Rise", "High Rise", "Low Rise"]
FADE = ["Light Fade", "Dark Fade"]
GENDERS = ["men", "women", "unisex"]
SIZES = ["S", "M", "L", "XL"]


def build() -> dict:
    categories = [{"id": i + 1, "title": t} for i, t in enumerate(CATS_TOP + CATS_BOTTOM)]
    cat_id = {c["title"]: c["id"] for c in categories}
    products, topwear, bottomwear = [], [], []
    for n in range(60):
        is_top = n % 2 == 0
        pid = f"p{n:03d}"
        cat = random.choice(CATS_TOP if is_top else CATS_BOTTOM)
        sizes = random.sample(SIZES, k=random.randint(2, 4))
        products.append({
            "id": pid,
            "product_title": f"{random.choice(STYLES)} {cat[:-1]} {n}",
            "product_description": "test product",
            "brand_id": f"brand_{n % 8}",
            "category_id": cat_id[cat],
            "gender": random.choice(GENDERS),
            "fashion_style_primary": random.choice(STYLES),
            "fashion_style_secondary": random.choice(STYLES),
            "fashion_sub_style": random.choice(SUB),
            "design": random.choice(DESIGN),
            "print_type": random.choice(["printed", "plain"]),
            "print_keywords": random.sample(["anime_manga", "graffiti", "abstract", "retro_vintage"],
                                            k=random.randint(0, 2)),
            "wash_type": random.choice(["none", "stone_wash", "acid_wash"]),
            "distressed": random.choice(["none", "ripped", "frayed_edges"]),
            "colour_name_majority": random.choice(["black", "white", "blue"]),
            "rgb_code_majority": random.choice(COLORS),
            "secondary_rgb_code": random.choice(COLORS),
            "gsm": random.choice([140, 180, 220, 260]),
            "selling_price": random.choice([699, 999, 1299, 1799, 2499]),
            "stretchable": random.random() > 0.5,
            "is_bestseller": random.random() > 0.7,
            "ranking_score": round(random.random(), 3),
            "status": "active",
            "items_available": random.randint(1, 50),
            "available_sizes": sizes,
            "items_available_in_each_size": [{"size": s, "qty": random.randint(0, 9), "color": "black"}
                                             for s in sizes],
            "images": [f"http://example.test/{pid}.jpg"],
            "created_at": "2026-06-01T00:00:00+00:00",
        })
        if is_top:
            topwear.append({"product_id": pid, "fitting_type": random.choice(TOPFIT),
                            "neck_type": random.choice(NECKS), "sleeves": random.choice(SLEEVES),
                            "length": random.choice(["Crop", "Regular", "Longline"]),
                            "hooded": random.random() > 0.7})
        else:
            bottomwear.append({"product_id": pid, "fitting_type": random.choice(BOTFIT),
                               "waist_type": random.choice(WAIST),
                               "length": random.choice(["Full Length", "Shorts", "Ankle Length"]),
                               "fade": random.choice(FADE),
                               "closure_type": random.choice(["Button", "Zip", "Drawstring"])})
    return {"products": products, "topwear": topwear, "bottomwear": bottomwear,
            "categories": categories}


if __name__ == "__main__":
    out = REPO_ROOT / "data" / "exports" / "catalog.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(build()), encoding="utf-8")
    print(f"Wrote synthetic catalog -> {out}")
