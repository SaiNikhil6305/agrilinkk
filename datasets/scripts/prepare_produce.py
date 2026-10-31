"""Fruit & vegetable quality dataset (3 grades) from openly licensed sources:
  FruitNet (Meshram & Patil 2022, CC-BY-4.0, via AgML): Apple, Banana, Guava, Lemon, Lime, Orange, Pomegranate; Good/Mixed/Bad
  Tomato quality (AgML, CC-BY-4.0): Fresh/Rotten
  jojogo9/freshness (MIT): apples, bananas, oranges; fresh/rotten
Grade mapping:  Premium <- Good / fresh ;  Good <- FruitNet 'Mixed' (sample holds both sound and defective fruit) ;  Fair <- Bad / rotten
Writes OUT/<grade>/<crop>__<source>_<n>.jpg and DEMO/<grade>/... (never used for training)."""
import io, os, sys, csv
import pyarrow.parquet as pq
from PIL import Image

RAW, OUT, DEMO = sys.argv[1:4]
CAP = int(os.environ.get("CAP", 80)); DEMO_N = int(os.environ.get("DEMO_N", 4))
cnt, dem = {}, {}
rows = []

def put(img_bytes, crop, grade, source, cap=CAP):
    k = (crop, grade, source)
    if dem.get(k, 0) < DEMO_N:
        d, n = DEMO, dem.get(k, 0); dem[k] = n + 1
    elif cnt.get(k, 0) < cap:
        d, n = OUT, cnt.get(k, 0); cnt[k] = n + 1
    else:
        return
    p = os.path.join(d, grade); os.makedirs(p, exist_ok=True)
    fn = f"{crop}__{source}_{n:04d}.jpg"
    try:
        Image.open(io.BytesIO(img_bytes)).convert("RGB").resize((224, 224)).save(os.path.join(p, fn), quality=90)
    except Exception:
        return
    rows.append((d == DEMO, grade, crop, source, fn))

def it(path, cols):
    for b in pq.ParquetFile(path).iter_batches(batch_size=64, columns=cols):
        yield from zip(*[b.column(c).to_pylist() for c in cols])

FN_CROPS = ['Apple', 'Banana', 'Guava', 'Lemon', 'Lime', 'Orange', 'Pomegranate']
FN_GRADE = {0: 'Fair', 1: 'Premium', 2: 'Good'}  # Bad, Good, Mixed
for f in sorted(os.listdir(f"{RAW}/fruitnet")):
    for img, lab, crop in it(f"{RAW}/fruitnet/{f}", ['image', 'label', 'crop']):
        put(img['bytes'], FN_CROPS[crop], FN_GRADE[lab], 'fruitnet')
for img, lab in it(f"{RAW}/tomato/raw.parquet", ['image', 'label']):
    put(img['bytes'], 'Tomato', 'Premium' if lab == 0 else 'Fair', 'agmltomato', cap=CAP * 3 // 2)
JJ = {0: ('Apple', 'Premium'), 1: ('Banana', 'Premium'), 2: ('Orange', 'Premium'), 3: ('Apple', 'Fair'), 4: ('Banana', 'Fair'), 5: ('Orange', 'Fair')}  # order from the parquet's embedded ClassLabel metadata (README order is wrong)
for f in ('train.parquet', 'test.parquet'):
    for img, lab in it(f"{RAW}/freshness/{f}", ['image', 'label']):
        crop, g = JJ[lab]
        put(img['bytes'], crop, g, 'freshness', cap=CAP // 2)  # second photographic style for apple/banana/orange
with open(os.path.join(OUT, '..', 'produce_manifest.csv'), 'w', newline='') as fh:
    csv.writer(fh).writerows([('demo', 'grade', 'crop', 'source', 'file')] + rows)
for d in (OUT, DEMO):
    print(d, {g: len(os.listdir(os.path.join(d, g))) for g in sorted(os.listdir(d))})
