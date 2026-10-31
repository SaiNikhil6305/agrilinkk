# Crop-quality model benchmark

Dataset: FruitNet (Meshram & Patil 2022, via AgML) + AgML Tomato quality + jojogo9/freshness (CC-BY-4.0 / CC-BY-4.0 / MIT). Mapping: Premium=Good/fresh; Good=FruitNet Mixed (sound and defective fruit together); Fair=Bad/rotten

Split: train 1342 / val 290 / test 288 (stratified, duplicates grouped). Selection by validation macro-F1.

| Model | Val macro-F1 | Test accuracy | Test macro-P | Test macro-R | Test macro-F1 | Params | Train s | CPU ms/img |
|---|---|---|---|---|---|---|---|---|
| VGG16 | 0.979 | 0.951 | 0.949 | 0.955 | 0.952 | 14.78M | 434 | 145.3 |
| ResNet50 **(selected)** | 0.984 | 0.958 | 0.956 | 0.963 | 0.959 | 23.85M | 207 | 51.0 |
| MobileNetV2 | 0.979 | 0.951 | 0.950 | 0.957 | 0.953 | 2.42M | 75 | 15.6 |
| EfficientNetB0 | 0.984 | 0.969 | 0.966 | 0.972 | 0.969 | 4.21M | 89 | 19.1 |
| CustomCNN | 0.913 | 0.885 | 0.885 | 0.895 | 0.888 | 0.26M | 751 | 4.7 |
