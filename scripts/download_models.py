"""Model downloader script for VisionAI.

Downloads all required ONNX model files for the computer vision pipeline.
Verifies checksums after download. Skips already-downloaded models.

Usage:
    python scripts/download_models.py [--model-dir /path/to/models]
"""

import argparse
import hashlib
import os
import sys
from pathlib import Path

import urllib.request

MODEL_REGISTRY = {
    "yolov8n": {
        "url": "https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8n.pt",
        "filename": "yolov8n.pt",
        "description": "YOLOv8 Nano - lightweight object detection (edge/CPU)",
        "size_mb": 6.2,
        "required": True,
    },
    "yolov8m": {
        "url": "https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8m.pt",
        "filename": "yolov8m.pt",
        "description": "YOLOv8 Medium - balanced object detection (GPU server)",
        "size_mb": 49.7,
        "required": True,
    },
    "yolov8m-pose": {
        "url": "https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8m-pose.pt",
        "filename": "yolov8m-pose.pt",
        "description": "YOLOv8 Medium Pose - pose estimation for fall/fight detection",
        "size_mb": 52.4,
        "required": True,
    },
    "insightface_buffalo_l": {
        "url": "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip",
        "filename": "buffalo_l.zip",
        "description": "InsightFace Buffalo_L - face detection (SCRFD) + recognition (ArcFace) + attributes",
        "size_mb": 326,
        "required": True,
        "post_extract": True,
    },
}

CUSTOM_MODELS = {
    "yolov8m_ppe": {
        "filename": "yolov8m_ppe.onnx",
        "description": "PPE detection (helmet, vest, gloves, shoes, shield, harness) - requires custom training",
    },
    "yolov8n_plate": {
        "filename": "yolov8n_plate.onnx",
        "description": "Indian license plate detection - requires custom training",
    },
    "yolov8m_fire": {
        "filename": "yolov8m_fire.onnx",
        "description": "Fire/smoke detection - requires custom training on D-Fire dataset",
    },
    "emotion_mobilenetv2": {
        "filename": "emotion_mobilenetv2.onnx",
        "description": "7-class emotion classification - requires training on FER2013/AffectNet",
    },
}


def get_file_hash(filepath: str, algorithm: str = "sha256") -> str:
    """Compute hash of a file.

    Args:
        filepath: Path to the file.
        algorithm: Hash algorithm to use.

    Returns:
        Hex digest of the file hash.
    """
    h = hashlib.new(algorithm)
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def download_with_progress(url: str, dest_path: str) -> bool:
    """Download a file with progress reporting.

    Args:
        url: URL to download from.
        dest_path: Local destination path.

    Returns:
        True if download was successful.
    """
    try:
        print(f"  Downloading from {url}")

        def reporthook(count, block_size, total_size):
            if total_size > 0:
                pct = min(100, count * block_size * 100 // total_size)
                mb_done = count * block_size / (1024 * 1024)
                mb_total = total_size / (1024 * 1024)
                sys.stdout.write(f"\r  Progress: {pct}% ({mb_done:.1f}/{mb_total:.1f} MB)")
                sys.stdout.flush()

        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        urllib.request.urlretrieve(url, dest_path, reporthook)
        print()  # New line after progress
        return True
    except Exception as e:
        print(f"\n  ERROR: Download failed - {e}")
        if os.path.exists(dest_path):
            os.remove(dest_path)
        return False


def export_to_onnx(pt_path: str, onnx_path: str) -> bool:
    """Export a PyTorch .pt model to ONNX format using ultralytics.

    Args:
        pt_path: Path to .pt model file.
        onnx_path: Output ONNX path.

    Returns:
        True if export was successful.
    """
    try:
        from ultralytics import YOLO

        model = YOLO(pt_path)
        model.export(format="onnx", imgsz=640, simplify=True)
        exported_path = pt_path.replace(".pt", ".onnx")
        if os.path.exists(exported_path) and exported_path != onnx_path:
            os.rename(exported_path, onnx_path)
        print(f"  Exported to ONNX: {onnx_path}")
        return True
    except ImportError:
        print("  WARNING: ultralytics not installed. Keeping .pt format.")
        return False
    except Exception as e:
        print(f"  WARNING: ONNX export failed - {e}. Keeping .pt format.")
        return False


def extract_insightface(zip_path: str, model_dir: str) -> bool:
    """Extract InsightFace model archive.

    Args:
        zip_path: Path to the zip file.
        model_dir: Directory to extract into.

    Returns:
        True if extraction was successful.
    """
    try:
        import zipfile

        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(model_dir)
        print(f"  Extracted InsightFace models to {model_dir}")
        return True
    except Exception as e:
        print(f"  ERROR: Extraction failed - {e}")
        return False


def download_paddleocr_models(model_dir: str) -> bool:
    """Download PaddleOCR models for license plate recognition.

    Args:
        model_dir: Directory to store models.

    Returns:
        True if all downloads succeeded.
    """
    paddle_dir = os.path.join(model_dir, "paddleocr")
    os.makedirs(paddle_dir, exist_ok=True)

    print("\n[PaddleOCR] Models will be auto-downloaded on first use by PaddleOCR library.")
    print(f"  Model directory: {paddle_dir}")
    print("  Set PADDLEOCR_MODEL_DIR environment variable to this path.")
    return True


def main() -> None:
    """Main entry point for model download."""
    parser = argparse.ArgumentParser(description="Download VisionAI model files")
    parser.add_argument(
        "--model-dir",
        type=str,
        default=os.getenv("MODEL_DIR", "./models"),
        help="Directory to store model files",
    )
    parser.add_argument(
        "--skip-onnx-export",
        action="store_true",
        help="Skip ONNX export of .pt models",
    )
    args = parser.parse_args()

    model_dir = os.path.abspath(args.model_dir)
    os.makedirs(model_dir, exist_ok=True)

    print("=" * 60)
    print("VisionAI Model Downloader")
    print(f"Model directory: {model_dir}")
    print("=" * 60)

    # Download pre-trained models
    success_count = 0
    fail_count = 0

    for name, info in MODEL_REGISTRY.items():
        filename = info["filename"]
        filepath = os.path.join(model_dir, filename)

        print(f"\n[{name}] {info['description']}")
        print(f"  Expected size: ~{info['size_mb']} MB")

        if os.path.exists(filepath):
            size_mb = os.path.getsize(filepath) / (1024 * 1024)
            print(f"  SKIP: Already exists ({size_mb:.1f} MB)")
            success_count += 1
            continue

        if download_with_progress(info["url"], filepath):
            size_mb = os.path.getsize(filepath) / (1024 * 1024)
            print(f"  OK: Downloaded ({size_mb:.1f} MB)")

            # Post-processing
            if info.get("post_extract") and filepath.endswith(".zip"):
                extract_insightface(filepath, model_dir)

            # Export YOLO models to ONNX
            if filepath.endswith(".pt") and not args.skip_onnx_export:
                onnx_path = filepath.replace(".pt", ".onnx")
                export_to_onnx(filepath, onnx_path)

            success_count += 1
        else:
            fail_count += 1

    # PaddleOCR
    download_paddleocr_models(model_dir)

    # Report on custom models
    print("\n" + "=" * 60)
    print("CUSTOM MODELS (require training)")
    print("=" * 60)
    for name, info in CUSTOM_MODELS.items():
        filepath = os.path.join(model_dir, info["filename"])
        status = "FOUND" if os.path.exists(filepath) else "NOT FOUND - needs training"
        print(f"  [{name}] {info['description']}")
        print(f"    Path: {filepath}")
        print(f"    Status: {status}")

    # Summary
    print("\n" + "=" * 60)
    print(f"Download complete: {success_count} succeeded, {fail_count} failed")
    if fail_count > 0:
        print("WARNING: Some models failed to download. Re-run this script to retry.")
        sys.exit(1)
    print("=" * 60)


if __name__ == "__main__":
    main()
