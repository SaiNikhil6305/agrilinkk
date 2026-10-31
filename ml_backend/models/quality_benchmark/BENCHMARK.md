# Crop-quality model benchmark

Dataset: Wild Rice Kernel Quality (Univ. of Manitoba) via AgML / Hugging Face Project-AgML/wild_rice_kernel_quality_classification (CC-BY-4.0). Mapping: Premium=healthy_large+healthy_medium; Good=broken_low+discoloured_low; Fair=broken_high+discoloured_high+insect_infected+unhulled

Split: train 1260 / val 270 / test 270 (stratified, duplicates grouped). Selection by validation macro-F1.

| Model | Val macro-F1 | Test accuracy | Test macro-P | Test macro-R | Test macro-F1 | Params | Train s | CPU ms/img |
|---|---|---|---|---|---|---|---|---|
| VGG16 | 0.945 | 0.937 | 0.938 | 0.937 | 0.937 | 14.78M | 411 | 155.1 |
| ResNet50 **(selected)** | 0.974 | 0.937 | 0.938 | 0.937 | 0.937 | 23.85M | 210 | 46.5 |
| MobileNetV2 | 0.933 | 0.933 | 0.933 | 0.933 | 0.933 | 2.42M | 79 | 22.3 |
| EfficientNetB0 | 0.963 | 0.959 | 0.959 | 0.959 | 0.959 | 4.21M | 74 | 16.7 |
| CustomCNN | 0.898 | 0.859 | 0.869 | 0.859 | 0.860 | 0.26M | 697 | 2.8 |
