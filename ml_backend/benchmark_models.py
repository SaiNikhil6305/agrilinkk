"""Benchmark five CNN architectures on the same crop-quality split and install the best one.

  python benchmark_models.py --data ../datasets/quality --out models --epochs 30

Models: Custom CNN (from scratch), VGG16, ResNet50, MobileNetV2, EfficientNetB0 (ImageNet, frozen backbone
+ identical head: GAP -> Dense(128, relu) -> Dropout(0.5) -> Dense(n, softmax)).
Protocol: one stratified 70/15/15 split with near-duplicates grouped (train_classifier.scan/split);
the winner is chosen on VALIDATION macro-F1 (never on test); every model is then reported on the untouched
TEST split: accuracy, macro precision/recall/F1, per-class metrics, confusion matrix, params, train time,
CPU inference time. The winner is saved as crop_quality_model_final.keras with its metadata.
"""
import argparse
import json
import os
import time
from datetime import datetime, timezone

import numpy as np
from PIL import Image

from preprocessing import preprocess
from train_classifier import scan, split

MODELS = {
    # name: (keras.applications ctor, preprocess kind, input size)
    "VGG16": ("VGG16", "caffe", 224),
    "ResNet50": ("ResNet50", "caffe", 224),
    "MobileNetV2": ("MobileNetV2", "mobilenet_v2", 224),
    "EfficientNetB0": ("EfficientNetB0", "none", 224),
    "CustomCNN": (None, "unit", 128),
}


def load(lst, size, flip=False):
    out = []
    for p, _ in lst:
        im = Image.open(p).convert("RGB").resize((size, size))
        if flip:
            im = im.transpose(Image.FLIP_LEFT_RIGHT)
        out.append(np.asarray(im, dtype=np.uint8))
    return np.stack(out)


def head(tf, inp_dim, n):
    i = tf.keras.Input((inp_dim,))
    x = tf.keras.layers.Dense(128, activation="relu")(i)
    x = tf.keras.layers.Dropout(0.5)(x)
    return tf.keras.Model(i, tf.keras.layers.Dense(n, activation="softmax")(x))


def custom_cnn(tf, size, n):
    L = tf.keras.layers
    i = tf.keras.Input((size, size, 3))
    x = i
    for f in (32, 64, 128):
        x = L.Conv2D(f, 3, padding="same", activation="relu")(x)
        x = L.BatchNormalization()(x)
        x = L.MaxPooling2D()(x)
    x = L.Conv2D(128, 3, padding="same", activation="relu")(x)
    x = L.GlobalAveragePooling2D()(x)
    x = L.Dense(128, activation="relu")(x)
    x = L.Dropout(0.5)(x)
    return tf.keras.Model(i, L.Dense(n, activation="softmax")(x))


def compile_(tf, m, lr):
    m.compile(tf.keras.optimizers.Adam(lr), loss=tf.keras.losses.SparseCategoricalCrossentropy(),
              metrics=[tf.keras.metrics.SparseCategoricalAccuracy(name="accuracy")])


def metrics(y, p, classes):
    from sklearn.metrics import accuracy_score, confusion_matrix, precision_recall_fscore_support
    P, R, F, S = precision_recall_fscore_support(y, p, labels=range(len(classes)), zero_division=0)
    mp, mr, mf, _ = precision_recall_fscore_support(y, p, average="macro", zero_division=0)
    return {"accuracy": round(float(accuracy_score(y, p)), 4), "macro_precision": round(float(mp), 4),
            "macro_recall": round(float(mr), 4), "macro_f1": round(float(mf), 4),
            "per_class": {c: {"precision": round(float(P[i]), 4), "recall": round(float(R[i]), 4),
                              "f1": round(float(F[i]), 4), "support": int(S[i])} for i, c in enumerate(classes)},
            "confusion_matrix": confusion_matrix(y, p, labels=range(len(classes))).tolist()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "models"))
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--only", default="")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--dataset-name", default="")
    ap.add_argument("--dataset-license", default="")
    ap.add_argument("--label-mapping", default="")
    ap.add_argument("--supported-crops", default="Rice,Paddy,Wild rice")
    ap.add_argument("--name", default="crop_quality_model_final")
    ap.add_argument("--bench-dir", default="quality_benchmark")
    a = ap.parse_args()

    import tensorflow as tf
    tf.random.set_seed(a.seed); np.random.seed(a.seed)
    classes, items, corrupt = scan(a.data, 0, a.seed)
    tr, va, te, dups = split(items, a.seed)
    ytr, yva, yte = (np.array([c for _, c in s]) for s in (tr, va, te))
    print(f"classes={classes} images={len(items)} dup_grouped={dups} train={len(tr)} val={len(va)} test={len(te)}", flush=True)
    os.makedirs(a.out, exist_ok=True)
    bench_dir = os.path.join(a.out, a.bench_dir)
    os.makedirs(bench_dir, exist_ok=True)
    counts = np.bincount(ytr, minlength=len(classes))
    cw = {i: len(ytr) / (len(classes) * max(n, 1)) for i, n in enumerate(counts)}
    cache, results, built = {}, {}, {}
    names = [n for n in MODELS if not a.only or n in a.only.split(",")]

    for name in names:
        ctor, pkind, size = MODELS[name]
        if size not in cache:
            cache.clear()  # only one input size held at a time
            cache[size] = {"tr": load(tr, size), "trf": load(tr, size, flip=True), "va": load(va, size), "te": load(te, size)}
        D = cache[size]
        print(f"\n=== {name} ===", flush=True)
        t0 = time.time()
        es = [tf.keras.callbacks.EarlyStopping(monitor="val_loss", patience=5, restore_best_weights=True)]
        if ctor is None:
            model = custom_cnn(tf, size, len(classes))
            compile_(tf, model, 1e-3)
            Xtr = preprocess(np.concatenate([D["tr"], D["trf"]]).astype(np.float32), pkind)
            h = model.fit(Xtr, np.concatenate([ytr, ytr]), validation_data=(preprocess(D["va"].astype(np.float32), pkind), yva),
                          epochs=a.epochs, batch_size=32, class_weight=cw, callbacks=es, verbose=2)
            full = model
            params = model.count_params()
        else:
            base = getattr(tf.keras.applications, ctor)(include_top=False, weights="imagenet", input_shape=(size, size, 3), pooling="avg")
            base.trainable = False
            feat = lambda X: base.predict(preprocess(X.astype(np.float32), pkind), batch_size=32, verbose=0)
            Ftr = np.concatenate([feat(D["tr"]), feat(D["trf"])])  # original + flipped copy as augmentation
            Fva = feat(D["va"])
            hd = head(tf, Ftr.shape[1], len(classes))
            compile_(tf, hd, 1e-3)
            h = hd.fit(Ftr, np.concatenate([ytr, ytr]), validation_data=(Fva, yva), epochs=a.epochs, batch_size=32,
                       class_weight=cw, callbacks=es, verbose=0)
            inp = tf.keras.Input((size, size, 3))
            full = tf.keras.Model(inp, hd(base(inp, training=False)))  # backbone + trained head = deployable model
            params = full.count_params()
        train_s = time.time() - t0
        Xva = preprocess(D["va"].astype(np.float32), pkind)
        Xte = preprocess(D["te"].astype(np.float32), pkind)
        pva = np.argmax(full.predict(Xva, batch_size=32, verbose=0), 1)
        t1 = time.time()
        pte = np.argmax(full.predict(Xte, batch_size=32, verbose=0), 1)
        infer_ms = 1000 * (time.time() - t1) / len(Xte)
        r = {"val": metrics(yva, pva, classes), "test": metrics(yte, pte, classes), "params": int(params),
             "train_seconds": round(train_s, 1), "cpu_inference_ms_per_image": round(infer_ms, 1),
             "epochs_run": len(h.history["loss"]), "input_size": size, "preprocess": pkind,
             "history": {k: [round(float(v), 4) for v in vs] for k, vs in h.history.items()}}
        results[name] = r
        # keep only the best-by-validation model in memory (5 full CNNs at once exhausts RAM on small machines)
        lead = max(results, key=lambda n: (results[n]["val"]["macro_f1"], results[n]["val"]["accuracy"]))
        if lead == name:
            built.clear(); built[name] = full
        else:
            del full
        import gc; gc.collect()
        print(f"{name}: val macroF1={r['val']['macro_f1']} | TEST acc={r['test']['accuracy']} macroF1={r['test']['macro_f1']} "
              f"| {params/1e6:.2f}M params | {train_s:.0f}s | {infer_ms:.1f} ms/img", flush=True)
        json.dump(results, open(os.path.join(bench_dir, "benchmark_results.json"), "w"), indent=1)

    best = max(results, key=lambda n: (results[n]["val"]["macro_f1"], results[n]["val"]["accuracy"]))
    print(f"\nBEST (by validation macro-F1): {best}", flush=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    path = os.path.join(a.out, a.name + ".keras")
    built[best].save(path)
    meta = {"classes": classes, "input_size": results[best]["input_size"], "preprocess": results[best]["preprocess"],
            "arch": best, "supported_crops": [c.strip() for c in a.supported_crops.split(",") if c.strip()], "version": f"{a.name}_{best}@{stamp}",
            "selection": "highest validation macro-F1 among 5 architectures; test split untouched until reporting",
            "dataset": {"name": a.dataset_name, "license": a.dataset_license, "label_mapping": a.label_mapping,
                        "images": len(items), "duplicates_grouped": dups,
                        "split": {"train": len(tr), "val": len(va), "test": len(te)}},
            "test_metrics": dict(results[best]["test"], test_images=len(te)),
            "benchmark": {n: {"val_macro_f1": r["val"]["macro_f1"], "test_accuracy": r["test"]["accuracy"],
                              "test_macro_precision": r["test"]["macro_precision"], "test_macro_recall": r["test"]["macro_recall"],
                              "test_macro_f1": r["test"]["macro_f1"], "params": r["params"], "train_seconds": r["train_seconds"],
                              "cpu_inference_ms_per_image": r["cpu_inference_ms_per_image"]} for n, r in results.items()}}
    json.dump(meta, open(os.path.join(a.out, a.name + ".json"), "w"), indent=1)
    # compact package: trained head only; the ImageNet backbone is rebuilt at load time
    if MODELS[best][0]:
        built[best].layers[-1].save(os.path.join(a.out, a.name + "_head.keras"))
        meta["package"] = {"format": "imagenet_backbone+head", "backbone": MODELS[best][0], "head_file": a.name + "_head.keras"}
        json.dump(meta, open(os.path.join(a.out, a.name + ".json"), "w"), indent=1)
        os.replace(path, os.path.join(a.out, "..", "..", "datasets", a.name + "_full.keras")) if os.path.isdir(os.path.join(a.out, "..", "..", "datasets")) else None

    # charts
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ns = list(results)
    fig, ax = plt.subplots(figsize=(10, 4.5))
    w = 0.2
    for k, (key, lbl) in enumerate([("accuracy", "Accuracy"), ("macro_precision", "Macro precision"), ("macro_recall", "Macro recall"), ("macro_f1", "Macro F1")]):
        ax.bar(np.arange(len(ns)) + (k - 1.5) * w, [results[n]["test"][key] for n in ns], w, label=lbl)
    ax.set_xticks(range(len(ns)), [n + (" ★" if n == best else "") for n in ns]); ax.set_ylim(0, 1); ax.legend(loc="lower right")
    ax.set_title(f"Crop-quality benchmark — held-out test set ({len(te)} images)"); fig.tight_layout()
    fig.savefig(os.path.join(bench_dir, "benchmark_comparison.png"), dpi=130)
    fig, axs = plt.subplots(1, len(ns), figsize=(3.4 * len(ns), 3.6))
    for axx, n in zip(np.atleast_1d(axs), ns):
        cm = np.array(results[n]["test"]["confusion_matrix"])
        axx.imshow(cm, cmap="Greens")
        axx.set_xticks(range(len(classes)), classes, rotation=45); axx.set_yticks(range(len(classes)), classes)
        for i in range(len(classes)):
            for j in range(len(classes)):
                axx.text(j, i, cm[i, j], ha="center", va="center", fontsize=9)
        axx.set_title(n + (" ★" if n == best else ""))
    fig.tight_layout(); fig.savefig(os.path.join(bench_dir, "confusion_matrices.png"), dpi=120)
    with open(os.path.join(bench_dir, "BENCHMARK.md"), "w") as fh:
        fh.write(f"# Crop-quality model benchmark\n\nDataset: {a.dataset_name} ({a.dataset_license}). Mapping: {a.label_mapping}\n\n"
                 f"Split: train {len(tr)} / val {len(va)} / test {len(te)} (stratified, duplicates grouped). Selection by validation macro-F1.\n\n"
                 "| Model | Val macro-F1 | Test accuracy | Test macro-P | Test macro-R | Test macro-F1 | Params | Train s | CPU ms/img |\n|---|---|---|---|---|---|---|---|---|\n")
        for n, r in results.items():
            fh.write(f"| {n}{' **(selected)**' if n == best else ''} | {r['val']['macro_f1']:.3f} | {r['test']['accuracy']:.3f} | {r['test']['macro_precision']:.3f} | "
                     f"{r['test']['macro_recall']:.3f} | {r['test']['macro_f1']:.3f} | {r['params']/1e6:.2f}M | {r['train_seconds']:.0f} | {r['cpu_inference_ms_per_image']:.1f} |\n")
    print(open(os.path.join(bench_dir, "BENCHMARK.md")).read())


if __name__ == "__main__":
    main()
