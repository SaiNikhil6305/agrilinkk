# Agri-Link · Kisan Quality & Fair Market Hub

AI crop-quality grading, FPO aggregation and transparent procurement connecting **farmers → FPOs/aggregators → institutional buyers**.

**Stack:** Vite + vanilla JS · FastAPI + Pydantic · SQLite (`agri_link.db`) · TensorFlow/Keras (MobileNetV2) · Firebase Auth (Google, phone OTP, email verification) · Open-Meteo · BigDataCloud/Nominatim · Agmarknet (data.gov.in) · Razorpay (optional)

## Quick start
```bash
pip install -r ml_backend/requirements.txt
npm install
cp .env.example .env        # fill in what you have
npm run dev                 # FastAPI :5000 + Vite :5173 → open http://localhost:5173
```
Production: `npm run build && cd ml_backend && python server.py` serves the site and the API on one port. The `Dockerfile` and `render.yaml` deploy the same thing; `render.yaml` mounts a persistent disk at `/data` so the SQLite file survives redeploys.

## What works, and what needs credentials
| Feature | Status |
|---|---|
| Email/phone + password accounts (PBKDF2-SHA256, 200k iterations, per-user salt), sessions with expiry, role locked at sign-up and enforced on every endpoint | Works |
| Google sign-in, phone OTP, email verification, password reset | Needs `FIREBASE_PROJECT_ID` on the server and the `VITE_FIREBASE_*` keys; add your domain under Firebase → Authentication → Authorized domains |
| Farmer: weather (current, 7-day, advisories), 14-day rainfall, GPS + reverse geocoding, scanner, produce registration, batch tracking, earnings, receipts, MSP, centres, 13-language UI | Works (browser calls Open-Meteo, BigDataCloud and Nominatim directly) |
| FPO: inflow with filters, yard inspection (accept/reject, grade, meter moisture), lot creation, adding batches, cancellation with stock release, buyer-demand accept/reject, matching, auto-assemble with reported shortfall, dispatch, payout recording with reasoned deductions, buyer-receipt confirmation, ledger | Works |
| Buyer: requirements (idempotent), explainable matching, marketplace, traceability before purchase, purchase orders, order tracking, delivery confirmation, invoice, expenditure vs MSP | Works |
| Live mandi prices (Agmarknet) | Needs a free `DATA_GOV_IN_API_KEY` from data.gov.in. Without it the page says so instead of showing stale data |
| Online payment | Needs Razorpay keys + webhook secret. The ledger is marked paid only by a signature-verified `payment.captured` webhook (duplicate-safe). Without keys, payments are recorded with bank/UPI reference numbers by the receiving party |
| Notifications | Stored in the DB for every workflow event; the UI polls every 15 s |

## Procurement rules (enforced in `server.py`)
- An allocation is a single guarded `UPDATE … WHERE available_quantity >= ?` inside a `BEGIN IMMEDIATE` transaction, so concurrent FPOs can never oversell (covered by tests).
- A lot is single-crop and single-grade, and a batch can join a lot only once. Procuring flips the lot `Available → Reserved` atomically, so only one buyer can win it.
- Money: farmer gross = qty × farmer's agreed rate; FPO margin = revenue − farmer cost; FPO net = margin − logistics. Every figure is derived from stored rows.
- Payables are created exactly once, at buyer-confirmed delivery. A transaction becomes `Settled` only when every farmer and the buyer are paid.

## AI pipeline
`ml_backend/train_classifier.py` handles any folder-per-class dataset:
- removes corrupt images
- groups duplicates with a 16×16 difference hash, so duplicates never straddle splits
- makes a stratified 70/15/15 split and augments the training set
- trains a frozen MobileNetV2 or EfficientNetB0 backbone with a head and class weights, with optional fine-tuning
- evaluates on the untouched test split

Each run writes `models/<name>.keras`, a `.json` with the real test metrics, a classification report, a confusion matrix and training curves.

| Model file | Purpose | Status |
|---|---|---|
| `crop_quality_model_final` | Grain quality → Premium/Good/Fair → Grade A/B/C | **Trained.** 5-model benchmark, ResNet50 selected; 93.7% test accuracy / 0.937 macro-F1 on 270 held-out images, 95.6% on 160 further unseen kernels. See `models/quality_benchmark/`. Shipped as the trained head (3 MB); the server rebuilds it with the standard ImageNet ResNet50 backbone, which Keras downloads once on first use |
| `produce_quality_model` | Fruit & vegetable quality (Apple, Banana, Guava, Lemon, Lime, Orange, Pomegranate, Tomato) → A/B/C | **Trained.** 5-model benchmark, ResNet50 selected (tied with EfficientNetB0 on validation macro-F1, won on validation accuracy); 95.8% test accuracy / 0.959 macro-F1 on 288 held-out images; 96.2% on 104 further unseen photos. See `models/produce_benchmark/` |
| `rice_variety_model` | Rice variety (Arborio/Basmati/Ipsala/Jasmine/Karacadag) | **Trained**: 97.4% test accuracy. Applied only to single milled grains on a dark background |
| `best_crop_type_model` | Jute/Maize/Rice/Sugarcane/Wheat | Not trained (Kaggle token needed). Without it, the farmer picks the crop |
| `best_maize_defect_model` | 8 maize kernel defects (maize only) | Needs a dataset |

### Quality model: dataset and benchmark
- **Dataset:** Wild Rice Kernel Quality (Univ. of Manitoba, CC-BY-4.0, via AgML on Hugging Face). Prepared with `datasets/scripts/prepare_wild_rice.py`: 1,800 images, 600 per grade, split 70/15/15.
- **Grade mapping:** Premium = healthy (large or medium); Good = low-severity broken or discoloured; Fair = high-severity broken or discoloured, insect-damaged, unhulled.
- **Benchmark:** `ml_backend/benchmark_models.py` compares a Custom CNN, VGG16, ResNet50, MobileNetV2 and EfficientNetB0 on the same split. The winner is chosen on validation macro-F1; the test set is used only for reporting.
- **Full GPU run:** `notebooks/AgriLink_quality_benchmark_colab.ipynb` (all 16,632 images).
- **Segregation:** `ml_backend/crop_segregation.py` files graded images into `crop_quality_results/Premium|Good|Bad`. Every portal's "Quality Segregation" page shows them, alongside the benchmark table.
- **Fruit & vegetable data:** FruitNet (CC-BY-4.0), AgML Tomato quality (CC-BY-4.0) and an apple/banana/orange freshness set (MIT), prepared with `datasets/scripts/prepare_produce.py`. Premium = good/fresh; Good = FruitNet "Mixed"; Fair = bad/rotten. The freshness set's README lists its labels in reverse order; the script uses the order stored in the data file.
- **Routing:** each crop goes to the quality model trained for it. Crops without a validated model (wheat, maize and others until trained) return NEEDS MANUAL INSPECTION. Wheat and maize slots (`wheat_quality_model`, `maize_quality_model`) activate as soon as their files are placed in `ml_backend/models/`.

The scanner never invents a result. Each missing model is reported as such. A grade is issued only from the quality model at ≥60% confidence; otherwise the result is **NEEDS MANUAL INSPECTION** and the FPO grades the batch at the yard. Moisture comes only from a physical meter reading. Grades are labelled "AI visual quality class", not AGMARK certificates.

Colab: upload the repo, `pip install -r ml_backend/requirements-train.txt`, download a dataset into `datasets/`, and run e.g. `python ml_backend/train_classifier.py --data datasets/crops --name best_crop_type_model --epochs 15 --fine_tune_epochs 5`. Then copy the outputs into `ml_backend/models/`.

## Tests
`cd ml_backend && python -m pytest -q test_workflow.py` covers:
- the full farmer → FPO → buyer → dispatch → delivery → settlement flow, with exact ledger arithmetic
- overselling, duplicate allocation, concurrent allocation and idempotent orders and requests
- delivery confirmed exactly once
- cross-role and cross-tenant access
- invalid images and honest no-model behaviour

## Reference data
MSP values come from CCEA decisions published by PIB, each stored with its source; re-verify every season. The procurement directory lists 30 well-known regulated markets by name, district and crops only, with no invented phone numbers. Extend `procurement_data.py`, for example from the e-NAM mandi list.

## API
Interactive docs at `/docs` (OpenAPI). Every endpoint from the original README is kept, plus `/api/procure`, `/api/transactions/{id}/dispatch`, `/api/transactions/{id}/invoice`, `/api/aggregator/inflow/{id}/review`, `/api/mandi-prices`, `/api/msp`, `/api/models`, `/api/notifications` and `/api/payments/razorpay/*`.
