"""Generic transfer-learning trainer + honest evaluator (MobileNetV2 or EfficientNetB0).

Dataset layout: DATA_DIR/<class_name>/*.jpg
Usage:
  python train_classifier.py --data ../datasets/crops --name best_crop_type_model --epochs 10
  python train_classifier.py --data ../datasets/maize_defects --name best_maize_defect_model
  python train_classifier.py --data ../datasets/quality --name crop_quality_model_final --arch efficientnet

Steps: drop corrupt images -> perceptual-hash dedup (near-duplicates are kept in the same split
to prevent leakage) -> stratified 70/15/15 split -> augmentation -> frozen backbone + head ->
optional fine-tune -> evaluation on the untouched test split.
Outputs in models/: <name>.keras, <name>.json (classes + real test metrics),
<name>_report.txt, <name>_confusion.png, <name>_curves.png
Run on a GPU (Colab) for full datasets; --max-per-class caps size for CPU runs.
"""
import argparse
import hashlib
import json
import os
import random
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
from PIL import Image, ImageEnhance

PREPROCESS = {"mobilenet": "mobilenet_v2", "efficientnet": "none"}


def preprocess(x, kind="mobilenet_v2"):
    """Shared with inference.py: MobileNetV2 expects [-1, 1]; EfficientNet rescales internally."""
    return x / 127.5 - 1.0 if kind == "mobilenet_v2" else x

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


def ahash(path, size=16):
    """16x16 difference hash: exact and visually identical images collide, while
    similar-looking but distinct grains do not."""
    im = Image.open(path).convert("L").resize((size + 1, size))
    a = np.asarray(im, dtype=np.int16)
    d = "".join("1" if v else "0" for v in (a[:, 1:] > a[:, :-1]).flatten())
    return d


def scan(data_dir, max_per_class, seed):
    classes = sorted(d for d in os.listdir(data_dir) if os.path.isdir(os.path.join(data_dir, d)))
    items, corrupt = [], 0
    rng = random.Random(seed)
    for ci, c in enumerate(classes):
        files = sorted(f for f in os.listdir(os.path.join(data_dir, c)) if f.lower().endswith(EXTS))
        rng.shuffle(files)
        if max_per_class:
            files = files[:max_per_class]
        for f in files:
            p = os.path.join(data_dir, c, f)
            try:
                with Image.open(p) as im:
                    im.verify()
                items.append((p, ci, ahash(p)))
            except Exception:
                corrupt += 1
    return classes, items, corrupt


def split(items, seed):
    """Group by perceptual hash so exact/near duplicates never straddle train and test."""
    groups = defaultdict(list)
    for p, ci, h in items:
        groups[(ci, h)].append((p, ci))
    by_class = defaultdict(list)
    for (ci, _), g in groups.items():
        by_class[ci].append(g)
    rng = random.Random(seed)
    tr, va, te = [], [], []
    for ci, gs in by_class.items():
        rng.shuffle(gs)
        n = len(gs)
        a, b = int(n * 0.7), int(n * 0.85)
        for g in gs[:a]: tr += g
        for g in gs[a:b]: va += g
        for g in gs[b:]: te += g
    dups = sum(len(g) - 1 for g in groups.values())
    return tr, va, te, dups


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--arch", choices=["mobilenet", "efficientnet"], default="mobilenet")
    ap.add_argument("--epochs", type=int, default=10)
    ap.add_argument("--fine_tune_epochs", type=int, default=0)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--size", type=int, default=224)
    ap.add_argument("--max-per-class", type=int, default=0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--dataset-name", default="")
    ap.add_argument("--dataset-license", default="")
    a = ap.parse_args()

    import tensorflow as tf
    from sklearn.metrics import classification_report, confusion_matrix, precision_recall_fscore_support, accuracy_score

    tf.random.set_seed(a.seed)
    classes, items, corrupt = scan(a.data, a.max_per_class, a.seed)
    tr, va, te, dups = split(items, a.seed)
    print(f"classes={classes} images={len(items)} corrupt_removed={corrupt} near_duplicates={dups} "
          f"train={len(tr)} val={len(va)} test={len(te)}")

    class ImageSeq(tf.keras.utils.PyDataset):
        """Loads images from disk per batch (memory-safe for full datasets)."""
        def __init__(self, lst, train):
            super().__init__(workers=1, use_multiprocessing=False)
            self.lst, self.train = list(lst), train
            self.rng = random.Random(a.seed)
            if train:
                self.rng.shuffle(self.lst)

        def __len__(self):
            return (len(self.lst) + a.batch - 1) // a.batch

        def __getitem__(self, i):
            chunk = self.lst[i * a.batch:(i + 1) * a.batch]
            imgs = []
            for p, _ in chunk:
                im = Image.open(p).convert("RGB").resize((a.size, a.size))
                if self.train:  # augmentation: flips, small rotation, brightness/contrast jitter
                    if self.rng.random() < .5: im = im.transpose(Image.FLIP_LEFT_RIGHT)
                    if self.rng.random() < .5: im = im.transpose(Image.FLIP_TOP_BOTTOM)
                    im = im.rotate(self.rng.uniform(-15, 15), resample=Image.BILINEAR)
                    im = ImageEnhance.Brightness(im).enhance(self.rng.uniform(.85, 1.15))
                    im = ImageEnhance.Contrast(im).enhance(self.rng.uniform(.85, 1.15))
                imgs.append(np.asarray(im, dtype=np.float32))
            x = preprocess(np.stack(imgs), PREPROCESS[a.arch])
            y = np.array([c for _, c in chunk], dtype=np.int32)
            return x, y

        def on_epoch_end(self):
            if self.train:
                self.rng.shuffle(self.lst)

    def ds(lst, train):
        return ImageSeq(lst, train)

    if a.arch == "mobilenet":
        base = tf.keras.applications.MobileNetV2(include_top=False, weights="imagenet", input_shape=(a.size, a.size, 3))
    else:
        base = tf.keras.applications.EfficientNetB0(include_top=False, weights="imagenet", input_shape=(a.size, a.size, 3))
    base.trainable = False
    inp = tf.keras.Input((a.size, a.size, 3))
    x = base(inp, training=False)
    x = tf.keras.layers.GlobalAveragePooling2D()(x)
    x = tf.keras.layers.Dense(128, activation="relu")(x)
    x = tf.keras.layers.Dropout(0.5)(x)
    out = tf.keras.layers.Dense(len(classes), activation="softmax")(x)
    model = tf.keras.Model(inp, out)

    # class weights against imbalance
    counts = np.bincount([c for _, c in tr], minlength=len(classes))
    cw = {i: len(tr) / (len(classes) * max(n, 1)) for i, n in enumerate(counts)}
    os.makedirs(OUT, exist_ok=True)
    ckpt = os.path.join(OUT, a.name + ".keras")
    cbs = [tf.keras.callbacks.ModelCheckpoint(ckpt, monitor="val_accuracy", save_best_only=True),
           tf.keras.callbacks.EarlyStopping(monitor="val_loss", patience=3, restore_best_weights=True)]
    model.compile(tf.keras.optimizers.Adam(1e-3), loss=tf.keras.losses.SparseCategoricalCrossentropy(), metrics=[tf.keras.metrics.SparseCategoricalAccuracy(name="accuracy")])
    h = model.fit(ds(tr, True), validation_data=ds(va, False), epochs=a.epochs, class_weight=cw, callbacks=cbs)
    hist = {k: list(map(float, v)) for k, v in h.history.items()}
    if a.fine_tune_epochs:
        base.trainable = True
        for l in base.layers[:-30]:
            l.trainable = False
        model.compile(tf.keras.optimizers.Adam(1e-5), loss=tf.keras.losses.SparseCategoricalCrossentropy(), metrics=[tf.keras.metrics.SparseCategoricalAccuracy(name="accuracy")])
        h2 = model.fit(ds(tr, True), validation_data=ds(va, False), epochs=a.fine_tune_epochs, class_weight=cw, callbacks=cbs)
        for k, v in h2.history.items():
            hist[k] += list(map(float, v))

    model = tf.keras.models.load_model(ckpt)
    y_true = [c for _, c in te]
    y_pred = np.argmax(model.predict(ds(te, False), verbose=0), axis=1).tolist()  # te order is preserved (train=False)
    p, r, f, s = precision_recall_fscore_support(y_true, y_pred, labels=range(len(classes)), zero_division=0)
    mp, mr, mf, _ = precision_recall_fscore_support(y_true, y_pred, average="macro", zero_division=0)
    cm = confusion_matrix(y_true, y_pred, labels=range(len(classes)))
    metrics = {"test_images": len(te), "accuracy": round(accuracy_score(y_true, y_pred), 4),
               "macro_precision": round(mp, 4), "macro_recall": round(mr, 4), "macro_f1": round(mf, 4),
               "per_class": {c: {"precision": round(float(p[i]), 4), "recall": round(float(r[i]), 4),
                                 "f1": round(float(f[i]), 4), "support": int(s[i])} for i, c in enumerate(classes)},
               "confusion_matrix": cm.tolist()}
    meta = {"classes": classes, "input_size": a.size, "arch": a.arch, "preprocess": PREPROCESS[a.arch],
            "version": f"{a.name}@{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}",
            "dataset": {"path": os.path.basename(os.path.abspath(a.data)), "name": a.dataset_name, "license": a.dataset_license,
                        "images": len(items), "corrupt_removed": corrupt, "near_duplicates_grouped": dups,
                        "split": {"train": len(tr), "val": len(va), "test": len(te)},
                        "max_per_class": a.max_per_class or None},
            "test_metrics": metrics, "history": hist}
    json.dump(meta, open(os.path.join(OUT, a.name + ".json"), "w"), indent=1)
    with open(os.path.join(OUT, a.name + "_report.txt"), "w") as fh:
        fh.write(classification_report(y_true, y_pred, labels=range(len(classes)), target_names=classes, zero_division=0))
        fh.write("\nConfusion matrix (rows=true, cols=pred):\n" + "\n".join(" ".join(f"{v:5d}" for v in row) for row in cm))

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(1.2 * len(classes) + 2, 1.2 * len(classes) + 1))
    ax.imshow(cm, cmap="Greens")
    ax.set_xticks(range(len(classes)), classes, rotation=45, ha="right"); ax.set_yticks(range(len(classes)), classes)
    for i in range(len(classes)):
        for j in range(len(classes)):
            ax.text(j, i, cm[i, j], ha="center", va="center")
    ax.set_xlabel("Predicted"); ax.set_ylabel("True"); ax.set_title(f"{a.name} — test confusion matrix")
    fig.tight_layout(); fig.savefig(os.path.join(OUT, a.name + "_confusion.png"), dpi=120)
    fig, axs = plt.subplots(1, 2, figsize=(10, 4))
    for k, axx in zip(("accuracy", "loss"), axs):
        axx.plot(hist[k], label="train"); axx.plot(hist["val_" + k], label="val"); axx.set_title(k); axx.legend()
    fig.tight_layout(); fig.savefig(os.path.join(OUT, a.name + "_curves.png"), dpi=120)
    print(json.dumps(metrics, indent=1))


if __name__ == "__main__":
    main()
