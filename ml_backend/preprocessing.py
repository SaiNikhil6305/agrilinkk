"""Image preprocessing shared by training (benchmark_models.py / train_classifier.py) and inference.py.
Keeping one implementation guarantees the server feeds models exactly what they were trained on."""
import numpy as np

_CAFFE_MEAN = np.array([103.939, 116.779, 123.68], dtype=np.float32)  # VGG16 / ResNet50 (BGR)


def preprocess(x: np.ndarray, kind: str) -> np.ndarray:
    """x: float32 array (..., H, W, 3) RGB in 0..255."""
    x = x.astype(np.float32)
    if kind == "mobilenet_v2":
        return x / 127.5 - 1.0
    if kind == "caffe":
        return x[..., ::-1] - _CAFFE_MEAN
    if kind == "unit":
        return x / 255.0
    return x  # "none": EfficientNet rescales internally
