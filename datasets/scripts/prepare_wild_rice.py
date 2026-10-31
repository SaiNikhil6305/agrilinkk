"""Build the 3-grade quality dataset from Wild Rice Kernel Quality (CC-BY-4.0, Univ. of Manitoba, via AgML/HF).
Grade mapping (documented, not learned):
  Premium <- healthy_large, healthy_medium
  Good    <- broken_low, discoloured_low            (minor defects)
  Fair    <- broken_high, discoloured_high, insect_infected, unhulled   (severe defects / rejection)
Also writes a separate never-trained-on demo set (20 per source class) for checking the model.
"""
import io, os, sys, random
import pyarrow.parquet as pq
from PIL import Image

SRC, OUT, DEMO = sys.argv[1], sys.argv[2], sys.argv[3]
NAMES = ['broken_high','broken_low','discoloured_high','discoloured_low','healthy_large','healthy_medium','insect_infected','unhulled']
GRADE = {'healthy_large':'Premium','healthy_medium':'Premium','broken_low':'Good','discoloured_low':'Good',
         'broken_high':'Fair','discoloured_high':'Fair','insect_infected':'Fair','unhulled':'Fair'}
CAP = {n: (300 if GRADE[n] != 'Fair' else 150) for n in NAMES}   # -> 600 images per grade
DEMO_N = 20
cnt = {n: 0 for n in NAMES}; demo = {n: 0 for n in NAMES}
files = sorted(f for f in os.listdir(SRC) if f.endswith('.parquet'))
for f in files:
    pf = pq.ParquetFile(os.path.join(SRC, f))
    for batch in pf.iter_batches(batch_size=64, columns=['image', 'label']):
        for img, lab in zip(batch.column('image').to_pylist(), batch.column('label').to_pylist()):
            n = NAMES[lab]
            if cnt[n] >= CAP[n] and demo[n] < DEMO_N:        # next kernels after the training cap -> unseen demo set
                d = os.path.join(DEMO, GRADE[n]); os.makedirs(d, exist_ok=True)
                Image.open(io.BytesIO(img['bytes'])).convert('RGB').resize((224, 224)).save(f"{d}/{n}_{demo[n]:03d}.jpg", quality=92)
                demo[n] += 1; continue
            if cnt[n] < CAP[n]:
                d = os.path.join(OUT, GRADE[n]); os.makedirs(d, exist_ok=True)
                Image.open(io.BytesIO(img['bytes'])).convert('RGB').resize((224, 224)).save(f"{d}/{n}_{cnt[n]:04d}.jpg", quality=92)
                cnt[n] += 1
    print(f, cnt, flush=True)
print('train pool', cnt); print('demo', demo)
