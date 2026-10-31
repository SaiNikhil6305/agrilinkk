"""Model loading, preprocessing and the transparent grading rules.

Models live in ml_backend/models/ as <name>.keras with a sibling <name>.json:
  {"classes": [...], "input_size": 224, "version": "...", "test_metrics": {...}}
The .json is written by train_classifier.py from the real evaluation run.
If a model file is missing, the endpoint says so — it never returns a made-up prediction.
"""
import io
import json
import os
import threading

import numpy as np
from PIL import Image, UnidentifiedImageError

from preprocessing import preprocess

MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")
MODEL_NAMES = {
    "crop": "best_crop_type_model",
    "defect": "best_maize_defect_model",
    "quality": "crop_quality_model_final",      # rice / paddy kernels
    "quality_produce": "produce_quality_model",  # fruits & vegetables
    "quality_wheat": "wheat_quality_model",      # added when trained
    "quality_maize": "maize_quality_model",      # added when trained
    "variety": "rice_variety_model",
}
QUALITY_KEYS = [k for k in MODEL_NAMES if k.startswith("quality")]


def supported_crops() -> dict:
    """crop (lower-case) -> quality model key, for every installed quality model."""
    out = {}
    for k in QUALITY_KEYS:
        st = model_status()[k]
        if st["installed"]:
            for c in st["meta"].get("supported_crops", []):
                out.setdefault(c.lower(), k)
    return out
MIN_CONFIDENCE = float(os.environ.get("MIN_PREDICTION_CONFIDENCE", "0.60"))
MAX_IMAGE_BYTES = 8 * 1024 * 1024

_lock = threading.Lock()
_cache: dict = {}


class ModelUnavailable(Exception):
    pass


class InvalidImage(Exception):
    pass


def model_status() -> dict:
    out = {}
    for key, name in MODEL_NAMES.items():
        meta_path = os.path.join(MODEL_DIR, name + ".json")
        meta = json.load(open(meta_path)) if os.path.exists(meta_path) else None
        head = (meta or {}).get("package", {}).get("head_file")
        present = os.path.exists(os.path.join(MODEL_DIR, name + ".keras")) or bool(head and os.path.exists(os.path.join(MODEL_DIR, head)))
        out[key] = {"file": name + ".keras", "installed": present and meta is not None, "meta": meta}
    return out


def _load(key: str):
    with _lock:
        if key in _cache:
            return _cache[key]
        name = MODEL_NAMES[key]
        path = os.path.join(MODEL_DIR, name + ".keras")
        meta_path = os.path.join(MODEL_DIR, name + ".json")
        if not (model_status()[key]["installed"]):
            _cache[key] = None
            return None
        try:
            import tensorflow as tf  # imported lazily: the API runs without TF installed
        except ImportError as e:
            raise ModelUnavailable("TensorFlow is not installed on this server") from e
        meta = json.load(open(meta_path))
        if os.path.exists(path):
            model = tf.keras.models.load_model(path, compile=False)
        else:
            # Compact package: standard ImageNet backbone (downloaded once by Keras) + our trained head.
            pk, size = meta["package"], int(meta.get("input_size", 224))
            base = getattr(tf.keras.applications, pk["backbone"])(include_top=False, weights="imagenet",
                                                                   input_shape=(size, size, 3), pooling="avg")
            head = tf.keras.models.load_model(os.path.join(MODEL_DIR, pk["head_file"]), compile=False)
            inp = tf.keras.Input((size, size, 3))
            model = tf.keras.Model(inp, head(base(inp, training=False)))
        _cache[key] = (model, meta)
        return _cache[key]


def load_image(data: bytes) -> Image.Image:
    if len(data) == 0:
        raise InvalidImage("Empty file")
    if len(data) > MAX_IMAGE_BYTES:
        raise InvalidImage("Image larger than 8 MB")
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
        img = Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError) as e:
        raise InvalidImage("File is not a readable image") from e
    if img.format not in ("JPEG", "PNG", "WEBP"):
        raise InvalidImage("Only JPEG, PNG or WEBP images are accepted")
    if min(img.size) < 64:
        raise InvalidImage("Image too small (min 64 px)")
    return img.convert("RGB")


def _run(key: str, img: Image.Image):
    loaded = _load(key)
    if loaded is None:
        return None
    model, meta = loaded
    size = int(meta.get("input_size", 224))
    arr = np.asarray(img.resize((size, size)), dtype=np.float32)[None, ...]
    arr = preprocess(arr, meta.get("preprocess", "none"))  # same function the training scripts use
    probs = model.predict(arr, verbose=0)[0]
    idx = int(np.argmax(probs))
    return {
        "label": meta["classes"][idx],
        "confidence": float(probs[idx]),
        "probabilities": {c: round(float(p), 4) for c, p in zip(meta["classes"], probs)},
        "version": meta.get("version", "unknown"),
    }


QUALITY_TO_GRADE = {"Premium": "A", "Good": "B", "Fair": "C"}
SEVERE_DEFECTS = {"Moldy", "Insect_Damaged", "Insect damaged", "Sprouted", "Heat_Damaged", "Heat damaged"}

ADVICE = {
    "A": "Meets Grade A on visual quality. Store dry, off the floor, and register for pooling.",
    "B": "Grade B (standard). Mechanical cleaning/sorting can lift value before pooling.",
    "C": "Grade C — rejection alert. Sort out damaged or rotten pieces, dry grain in shade to safe moisture, "
         "and rescan before sale.",
    "MANUAL": "Not enough evidence for an automatic grade. The FPO will inspect this batch at the yard.",
}


def predict(img: Image.Image, crop_hint: str | None, measured_moisture: float | None) -> dict:
    status = model_status()
    if not any(s["installed"] for s in status.values()):
        raise ModelUnavailable("No trained models are installed on this server (ml_backend/models/).")

    crop = _run("crop", img)
    crop_label = crop["label"] if crop and crop["confidence"] >= MIN_CONFIDENCE else None
    effective_crop = crop_hint or crop_label

    defect = None
    if effective_crop and effective_crop.lower() == "maize":
        defect = _run("defect", img)  # defect model is trained on maize kernels only

    variety, variety_skipped = None, False
    if effective_crop and effective_crop.lower() in ("rice", "paddy"):
        # Trained only on single milled-rice grains photographed on a black background (Rice Image Dataset).
        # Outside those capture conditions its softmax is meaningless, so it is not applied.
        g = np.asarray(img.convert("L").resize((64, 64)), dtype=np.float32)
        border = np.concatenate([g[:4].ravel(), g[-4:].ravel(), g[:, :4].ravel(), g[:, -4:].ravel()]).mean()
        if border < 50:
            variety = _run("variety", img)
        elif model_status()["variety"]["installed"]:
            variety_skipped = True

    reasons = []
    grade = "MANUAL"
    quality = None
    routes = supported_crops()
    if not effective_crop:
        reasons.append("Select the crop so the right quality model can be applied")
    elif effective_crop.lower() not in routes:
        names = sorted({c.title() for c in routes})
        reasons.append(f"No validated quality model for {effective_crop} yet (graded crops: {', '.join(names) or 'none'}); "
                       "it needs yard inspection")
    else:
        quality = _run(routes[effective_crop.lower()], img)
        quality["model"] = routes[effective_crop.lower()]
    if quality and quality["confidence"] >= MIN_CONFIDENCE:
        grade = QUALITY_TO_GRADE.get(quality["label"], "MANUAL")
        reasons.append(f"Quality model: {quality['label']} ({quality['confidence']:.0%})")
    elif quality:
        reasons.append(f"Quality model confidence {quality['confidence']:.0%} is below {MIN_CONFIDENCE:.0%}")
    elif not reasons:
        reasons.append("Quality model not installed")

    if defect and defect["confidence"] >= MIN_CONFIDENCE and defect["label"] != "Normal":
        reasons.append(f"Maize defect detected: {defect['label']} ({defect['confidence']:.0%})")
        if defect["label"] in SEVERE_DEFECTS:
            grade = "C"
        elif grade == "A":
            grade = "B"

    if crop_hint and crop_label and crop_hint.lower() != crop_label.lower():
        reasons.append(f"You selected {crop_hint} but the image looks like {crop_label}")
        grade = "MANUAL"

    versions = {k: v["version"] for k, v in (("crop", crop), ("defect", defect), ("quality", quality), ("variety", variety)) if v}
    if variety_skipped:
        reasons.append("Variety not identified: the variety model only works on single milled grains on a dark background")
    if variety and variety["confidence"] < MIN_CONFIDENCE:
        reasons.append(f"Variety uncertain (top guess {variety['label']} {variety['confidence']:.0%})")
    return {
        "crop": crop, "effective_crop": effective_crop, "defect": defect, "variety": variety, "quality": quality,
        "grade": grade,
        "grade_label": {"A": "Grade A (Premium)", "B": "Grade B (Good)", "C": "Grade C (Fair)",
                        "MANUAL": "NEEDS MANUAL INSPECTION"}[grade],
        "grade_basis": "AI visual quality class (not an official AGMARK certificate)",
        "rejection_alert": grade == "C",
        "reasons": reasons,
        "advisory": ADVICE[grade] if not (grade == "A" and quality and quality.get("model") == "quality_produce")
                    else "Meets Grade A on visual quality. Keep shaded and ventilated, and move to market or cold storage quickly.",
        "moisture": ({"value": measured_moisture, "source": "Entered by farmer from moisture meter"}
                     if measured_moisture is not None else
                     {"value": None, "source": "Not measured — moisture cannot be read from a photo"}),
        "model_version": versions,
    }
