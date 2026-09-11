"""
ONNX Inference Engine for VisionAI.

Provides a unified wrapper around ONNX Runtime inference sessions with
standardised pre-processing (letterboxing, normalisation, channel
swapping) and post-processing (NMS, coordinate scaling) utilities.

All functions operate on NumPy arrays in BGR colour space (OpenCV
convention) unless otherwise noted.

Usage::

    from app.cv.inference_engine import ONNXInferenceEngine

    engine = ONNXInferenceEngine(session)
    tensor = engine.preprocess(image, input_size=(640, 640))
    outputs = engine.infer(tensor)
"""

from __future__ import annotations

from typing import Optional, Sequence

import cv2
import numpy as np
import onnxruntime as ort
import structlog

logger = structlog.stdlib.get_logger(__name__)


def letterbox(
    image: np.ndarray,
    target_size: tuple[int, int] = (640, 640),
    color: tuple[int, int, int] = (114, 114, 114),
    auto: bool = False,
    scale_fill: bool = False,
    stride: int = 32,
) -> tuple[np.ndarray, float, tuple[float, float]]:
    """Resize and pad an image to ``target_size`` while preserving the
    aspect ratio.

    The image is resized so that the longest side fits within the target
    dimension, then padded symmetrically with the specified colour.

    Args:
        image: Input BGR image as a NumPy array (H, W, C).
        target_size: Desired ``(width, height)`` of the output.
        color: Padding colour as ``(B, G, R)``.  Defaults to grey.
        auto: If ``True``, compute minimal padding aligned to ``stride``.
        scale_fill: If ``True``, stretch (do not preserve aspect ratio).
        stride: Alignment stride when ``auto=True``.

    Returns:
        tuple: A 3-tuple of:
            - **padded** (*np.ndarray*) -- The letterboxed image.
            - **ratio** (*float*) -- The scale ratio applied.
            - **pad** (*tuple[float, float]*) -- The ``(dw, dh)``
              padding added on each side.
    """
    src_h, src_w = image.shape[:2]
    target_w, target_h = target_size

    # Compute scale ratio
    ratio = min(target_w / src_w, target_h / src_h)

    # Compute scaled (unpadded) size
    new_w = int(round(src_w * ratio))
    new_h = int(round(src_h * ratio))

    # Compute padding
    dw = target_w - new_w
    dh = target_h - new_h

    if auto:
        # Minimal padding aligned to stride
        dw = dw % stride
        dh = dh % stride
    elif scale_fill:
        # Stretch to fill
        dw, dh = 0, 0
        new_w, new_h = target_w, target_h
        ratio = min(target_w / src_w, target_h / src_h)

    # Split padding evenly on both sides
    dw_half = dw / 2.0
    dh_half = dh / 2.0

    # Resize the image
    if (new_w, new_h) != (src_w, src_h):
        interp = cv2.INTER_LINEAR if ratio > 1.0 else cv2.INTER_AREA
        resized = cv2.resize(image, (new_w, new_h), interpolation=interp)
    else:
        resized = image

    # Add border padding
    top = int(round(dh_half - 0.1))
    bottom = int(round(dh_half + 0.1))
    left = int(round(dw_half - 0.1))
    right = int(round(dw_half + 0.1))

    padded = cv2.copyMakeBorder(
        resized, top, bottom, left, right,
        cv2.BORDER_CONSTANT, value=color,
    )

    return padded, ratio, (dw_half, dh_half)


def nms(
    boxes: np.ndarray,
    scores: np.ndarray,
    iou_threshold: float = 0.45,
) -> np.ndarray:
    """Non-Maximum Suppression on bounding boxes.

    Args:
        boxes: Array of shape ``(N, 4)`` with ``[x1, y1, x2, y2]``
            coordinates per row.
        scores: Array of shape ``(N,)`` with confidence scores.
        iou_threshold: IoU threshold above which overlapping boxes are
            suppressed.  Defaults to ``0.45``.

    Returns:
        np.ndarray: Integer array of indices to keep, sorted by
            descending score.
    """
    if len(boxes) == 0:
        return np.array([], dtype=np.int64)

    x1 = boxes[:, 0]
    y1 = boxes[:, 1]
    x2 = boxes[:, 2]
    y2 = boxes[:, 3]

    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]

    keep: list[int] = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))

        if order.size == 1:
            break

        # Compute IoU of the picked box with the rest
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])

        inter_w = np.maximum(0.0, xx2 - xx1)
        inter_h = np.maximum(0.0, yy2 - yy1)
        intersection = inter_w * inter_h

        union = areas[i] + areas[order[1:]] - intersection
        iou = np.where(union > 0, intersection / union, 0.0)

        # Keep boxes with IoU below the threshold
        inds = np.where(iou <= iou_threshold)[0]
        order = order[inds + 1]

    return np.array(keep, dtype=np.int64)


def xywh2xyxy(boxes: np.ndarray) -> np.ndarray:
    """Convert bounding boxes from ``[cx, cy, w, h]`` to
    ``[x1, y1, x2, y2]`` format.

    Args:
        boxes: Array of shape ``(N, 4)`` in centre-width-height format.

    Returns:
        np.ndarray: Array of shape ``(N, 4)`` in corner format.
    """
    xyxy = np.empty_like(boxes)
    half_w = boxes[:, 2] / 2.0
    half_h = boxes[:, 3] / 2.0
    xyxy[:, 0] = boxes[:, 0] - half_w  # x1
    xyxy[:, 1] = boxes[:, 1] - half_h  # y1
    xyxy[:, 2] = boxes[:, 0] + half_w  # x2
    xyxy[:, 3] = boxes[:, 1] + half_h  # y2
    return xyxy


def xyxy2xywh(boxes: np.ndarray) -> np.ndarray:
    """Convert bounding boxes from ``[x1, y1, x2, y2]`` to
    ``[cx, cy, w, h]`` format.

    Args:
        boxes: Array of shape ``(N, 4)`` in corner format.

    Returns:
        np.ndarray: Array of shape ``(N, 4)`` in centre-width-height format.
    """
    xywh = np.empty_like(boxes)
    xywh[:, 0] = (boxes[:, 0] + boxes[:, 2]) / 2.0  # cx
    xywh[:, 1] = (boxes[:, 1] + boxes[:, 3]) / 2.0  # cy
    xywh[:, 2] = boxes[:, 2] - boxes[:, 0]           # w
    xywh[:, 3] = boxes[:, 3] - boxes[:, 1]           # h
    return xywh


def scale_boxes(
    boxes: np.ndarray,
    from_shape: tuple[int, int],
    to_shape: tuple[int, int],
    ratio: float | None = None,
    pad: tuple[float, float] | None = None,
) -> np.ndarray:
    """Rescale bounding boxes from letterboxed coordinates back to the
    original image coordinates.

    Args:
        boxes: Array of shape ``(N, 4)`` in ``[x1, y1, x2, y2]`` format,
            relative to ``from_shape``.
        from_shape: The letterboxed image dimensions ``(height, width)``.
        to_shape: The original image dimensions ``(height, width)``.
        ratio: The scale ratio from letterboxing.  If ``None`` it is
            recomputed from the shapes.
        pad: The ``(dw, dh)`` padding from letterboxing.  If ``None``
            it is recomputed from the shapes.

    Returns:
        np.ndarray: Rescaled boxes of shape ``(N, 4)`` clipped to the
            original image boundaries.
    """
    if boxes.shape[0] == 0:
        return boxes

    src_h, src_w = from_shape
    dst_h, dst_w = to_shape

    if ratio is None or pad is None:
        ratio = min(src_w / dst_w, src_h / dst_h)
        pad_w = (src_w - dst_w * ratio) / 2.0
        pad_h = (src_h - dst_h * ratio) / 2.0
    else:
        pad_w, pad_h = pad

    scaled = boxes.copy().astype(np.float32)
    scaled[:, 0] = (scaled[:, 0] - pad_w) / ratio  # x1
    scaled[:, 1] = (scaled[:, 1] - pad_h) / ratio  # y1
    scaled[:, 2] = (scaled[:, 2] - pad_w) / ratio  # x2
    scaled[:, 3] = (scaled[:, 3] - pad_h) / ratio  # y2

    # Clip to image boundaries
    scaled[:, 0] = np.clip(scaled[:, 0], 0, dst_w)
    scaled[:, 1] = np.clip(scaled[:, 1], 0, dst_h)
    scaled[:, 2] = np.clip(scaled[:, 2], 0, dst_w)
    scaled[:, 3] = np.clip(scaled[:, 3], 0, dst_h)

    return scaled


def scale_keypoints(
    keypoints: np.ndarray,
    ratio: float,
    pad: tuple[float, float],
) -> np.ndarray:
    """Rescale keypoints from letterboxed coordinates back to the
    original image coordinates.

    Args:
        keypoints: Array of shape ``(N, K, 2)`` or ``(N, K, 3)``
            where K is the number of keypoints.
        ratio: The scale ratio from letterboxing.
        pad: The ``(dw, dh)`` padding from letterboxing.

    Returns:
        np.ndarray: Rescaled keypoints.
    """
    if keypoints.size == 0:
        return keypoints

    scaled = keypoints.copy().astype(np.float32)
    scaled[..., 0] = (scaled[..., 0] - pad[0]) / ratio
    scaled[..., 1] = (scaled[..., 1] - pad[1]) / ratio
    return scaled


class ONNXInferenceEngine:
    """High-level wrapper around an ONNX Runtime inference session.

    Encapsulates the common pattern of preprocess -> infer -> postprocess
    with configurable normalisation, channel ordering, and input sizing.

    Attributes:
        session: The underlying ONNX Runtime session.
        input_name: Name of the first model input tensor.
        input_shape: Expected shape of the first model input.
        output_names: Names of all model output tensors.
    """

    def __init__(self, session: ort.InferenceSession) -> None:
        """Initialise the engine from a loaded ONNX Runtime session.

        Args:
            session: A loaded ``ort.InferenceSession``.
        """
        self.session = session

        # Cache input/output metadata
        model_inputs = session.get_inputs()
        self.input_name: str = model_inputs[0].name
        self.input_shape: list = model_inputs[0].shape
        self.input_type: str = model_inputs[0].type

        self.output_names: list[str] = [
            out.name for out in session.get_outputs()
        ]

        logger.debug(
            "inference_engine.initialised",
            input_name=self.input_name,
            input_shape=self.input_shape,
            output_names=self.output_names,
        )

    def preprocess(
        self,
        image: np.ndarray,
        input_size: tuple[int, int] = (640, 640),
        normalize: bool = True,
        swap_rb: bool = True,
        mean: tuple[float, float, float] = (0.0, 0.0, 0.0),
        std: tuple[float, float, float] = (1.0, 1.0, 1.0),
    ) -> tuple[np.ndarray, float, tuple[float, float]]:
        """Preprocess an image for model inference.

        Performs letterboxing, optional BGR-to-RGB conversion,
        normalisation, and HWC-to-NCHW transposition.

        Args:
            image: Input BGR image as a NumPy array (H, W, C).
            input_size: Model input size as ``(width, height)``.
                Defaults to ``(640, 640)``.
            normalize: If ``True``, scale pixel values to ``[0, 1]``
                and optionally apply mean/std normalisation.
            swap_rb: If ``True``, swap red and blue channels
                (BGR to RGB).  Defaults to ``True``.
            mean: Per-channel mean for normalisation.
            std: Per-channel standard deviation for normalisation.

        Returns:
            tuple: A 3-tuple of:
                - **tensor** (*np.ndarray*) -- Preprocessed tensor of
                  shape ``(1, 3, H, W)`` as float32.
                - **ratio** (*float*) -- The letterbox scale ratio.
                - **pad** (*tuple[float, float]*) -- The ``(dw, dh)``
                  letterbox padding.
        """
        # Letterbox to target size
        padded, ratio, pad = letterbox(image, target_size=input_size)

        # BGR -> RGB
        if swap_rb:
            padded = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB)

        # HWC -> CHW and add batch dimension
        tensor = padded.astype(np.float32)

        if normalize:
            tensor = tensor / 255.0
            # Apply mean/std normalisation
            mean_arr = np.array(mean, dtype=np.float32).reshape(1, 1, 3)
            std_arr = np.array(std, dtype=np.float32).reshape(1, 1, 3)
            tensor = (tensor - mean_arr) / std_arr

        # HWC -> CHW -> NCHW
        tensor = tensor.transpose(2, 0, 1)
        tensor = np.expand_dims(tensor, axis=0)

        # Ensure contiguous memory layout
        tensor = np.ascontiguousarray(tensor)

        return tensor, ratio, pad

    def preprocess_face(
        self,
        face_image: np.ndarray,
        input_size: tuple[int, int] = (112, 112),
    ) -> np.ndarray:
        """Preprocess an aligned face image for recognition models.

        Resizes to ``input_size``, converts BGR to RGB, normalises to
        ``[-1, 1]``, and transposes to NCHW.

        Args:
            face_image: Aligned face image as a BGR NumPy array.
            input_size: Target face size ``(width, height)``.

        Returns:
            np.ndarray: Preprocessed tensor of shape ``(1, 3, H, W)``.
        """
        resized = cv2.resize(face_image, input_size, interpolation=cv2.INTER_LINEAR)
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        tensor = rgb.astype(np.float32)
        # Normalise to [-1, 1]
        tensor = (tensor - 127.5) / 127.5
        # HWC -> CHW -> NCHW
        tensor = tensor.transpose(2, 0, 1)
        tensor = np.expand_dims(tensor, axis=0)
        return np.ascontiguousarray(tensor)

    def infer(
        self,
        tensor: np.ndarray,
        output_names: list[str] | None = None,
    ) -> list[np.ndarray]:
        """Run inference on a preprocessed input tensor.

        Args:
            tensor: Input tensor of shape ``(1, C, H, W)`` as float32.
            output_names: Optional subset of output names to retrieve.
                If ``None``, all outputs are returned.

        Returns:
            list[np.ndarray]: Model output arrays in the order defined
                by the model graph (or ``output_names`` if specified).
        """
        names = output_names or self.output_names
        feed = {self.input_name: tensor}

        try:
            outputs = self.session.run(names, feed)
        except Exception as exc:
            logger.error(
                "inference_engine.infer_failed",
                input_shape=tensor.shape,
                error=str(exc),
            )
            raise RuntimeError(f"ONNX inference failed: {exc}") from exc

        return outputs

    def warmup(self, input_size: tuple[int, int] = (640, 640)) -> None:
        """Run a dummy inference pass to warm up the execution provider.

        This pre-allocates GPU memory and triggers JIT compilation for
        providers like TensorRT.

        Args:
            input_size: Input dimensions ``(width, height)``.
        """
        dummy = np.random.rand(1, 3, input_size[1], input_size[0]).astype(np.float32)
        try:
            self.session.run(self.output_names, {self.input_name: dummy})
            logger.info("inference_engine.warmup_complete", input_size=input_size)
        except Exception as exc:
            logger.warning(
                "inference_engine.warmup_failed",
                error=str(exc),
            )
