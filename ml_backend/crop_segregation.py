"""Automated crop segregation: file graded images into crop_quality_results/{Premium,Good,Bad} with a
SHA-256-deduplicated JSON ledger. Used by the /api/predict endpoint and by the batch CLI below.

Batch CLI (runs the installed quality model over a folder of images it has never trained on):
  python crop_segregation.py ../datasets/demo_quality            # subfolders = true grade (optional)
"""
import hashlib
import json
import os
import sys
import threading
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(ROOT, "crop_quality_results")
META = os.path.join(RESULTS_DIR, "metadata.json")
FOLDER_FOR_GRADE = {"A": "Premium", "B": "Good", "C": "Bad"}
_lock = threading.Lock()


def _load() -> dict:
    return json.load(open(META)) if os.path.exists(META) else {}


def save_image(data: bytes, grade: str, info: dict) -> str | None:
    """Store image bytes in its grade folder; returns 'Folder/file.jpg' or None if not segregable."""
    if grade not in FOLDER_FOR_GRADE:
        return None
    sha = hashlib.sha256(data).hexdigest()
    folder = FOLDER_FOR_GRADE[grade]
    os.makedirs(os.path.join(RESULTS_DIR, folder), exist_ok=True)
    fname = f"{sha[:16]}.jpg"
    with _lock:
        meta = _load()
        old = meta.get(sha)
        if old and old["file"] != f"{folder}/{fname}":  # re-graded: move, never duplicate
            try:
                os.remove(os.path.join(RESULTS_DIR, old["file"]))
            except FileNotFoundError:
                pass
        path = os.path.join(RESULTS_DIR, folder, fname)
        if not os.path.exists(path):
            with open(path, "wb") as f:
                f.write(data)
        if old:  # keep the known ground truth / provenance of an image that is scanned again
            for k in ("true_grade", "source_label"):
                if info.get(k) is None and old.get(k) is not None:
                    info[k] = old[k]
            if old.get("true_grade") and "farmer scan" in str(info.get("source")):
                info["source"] = old.get("source")
        meta[sha] = {"file": f"{folder}/{fname}", "grade": grade, "bytes": len(data),
                     "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), **info}
        tmp = META + ".tmp"
        json.dump(meta, open(tmp, "w"), indent=1)
        os.replace(tmp, META)
    return f"{folder}/{fname}"


def summary() -> dict:
    meta = _load()
    out = {f: {"count": 0, "items": []} for f in FOLDER_FOR_GRADE.values()}
    for sha, m in sorted(meta.items(), key=lambda kv: kv[1]["timestamp"], reverse=True):
        folder, fname = m["file"].split("/")
        if os.path.exists(os.path.join(RESULTS_DIR, m["file"])):
            out[folder]["count"] += 1
            out[folder]["items"].append({"file": fname, **{k: m.get(k) for k in
                                         ("crop", "grade", "confidence", "probabilities", "true_grade", "source", "source_label", "timestamp")}})
    total = sum(v["count"] for v in out.values()) or 1
    for v in out.values():
        v["percent"] = round(100 * v["count"] / total, 1)
    labelled = [i for v in out.values() for i in v["items"] if i.get("true_grade")]
    acc = (sum(i["true_grade"] == i["grade"] for i in labelled) / len(labelled)) if labelled else None
    return {"folders": out, "labelled_checked": len(labelled), "labelled_accuracy": acc,
            "manual_inspection": meta_manual()}


def meta_manual() -> int:
    p = os.path.join(RESULTS_DIR, "manual_inspection.json")
    return len(json.load(open(p))) if os.path.exists(p) else 0


if __name__ == "__main__":
    import inference
    src = sys.argv[1]
    to_grade = {"Premium": "A", "Good": "B", "Fair": "C", "Bad": "C"}
    n = ok = 0
    manual = []
    for root, _, files in os.walk(src):
        true = to_grade.get(os.path.basename(root))
        for f in sorted(files):
            if not f.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                continue
            data = open(os.path.join(root, f), "rb").read()
            crop = f.split("__")[0] if "__" in f else "Rice"  # produce files are named <Crop>__<source>_n.jpg
            r = inference.predict(inference.load_image(data), crop, None)
            q = r["quality"]
            info = {"crop": "Wild rice kernel" if crop == "Rice" else crop, "confidence": round(q["confidence"], 4), "probabilities": q["probabilities"],
                    "source": "held-out demo image (never used in training)", "source_label": f.rsplit("_", 1)[0].replace("__", " · "), "true_grade": true}
            if r["grade"] in FOLDER_FOR_GRADE:
                save_image(data, r["grade"], info)
            else:
                manual.append({"file": f, "true_grade": true, "confidence": info["confidence"]})
            n += 1; ok += (true == r["grade"])
            print(f"{f:28s} true={true} predicted={r['grade_label']:<28s} conf={q['confidence']:.2f}")
    mp = os.path.join(RESULTS_DIR, "manual_inspection.json")
    prev = json.load(open(mp)) if os.path.exists(mp) else []
    names = {m["file"] for m in manual}
    json.dump([m for m in prev if m["file"] not in names] + manual, open(mp, "w"), indent=1)
    print(f"\n{n} images checked ({len(manual)} sent to manual inspection); {ok}/{n} = {ok / max(n, 1):.1%} match their true grade")
