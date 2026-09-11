"""Tests for face detection and recognition pipeline."""

import numpy as np
import pytest


class TestFaceDetection:
    """Test face detection utilities."""

    def test_face_alignment_transform(self) -> None:
        """Test that face alignment produces correct output shape."""
        # Create a dummy 640x480 RGB image
        image = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)

        # Standard 5-point landmarks (approximate positions)
        landmarks = np.array([
            [250.0, 200.0],  # left eye
            [390.0, 200.0],  # right eye
            [320.0, 260.0],  # nose
            [260.0, 310.0],  # left mouth
            [380.0, 310.0],  # right mouth
        ], dtype=np.float32)

        import cv2

        # ArcFace reference points for 112x112
        dst = np.array([
            [38.2946, 51.6963],
            [73.5318, 51.5014],
            [56.0252, 71.7366],
            [41.5493, 92.3655],
            [70.7299, 92.2041],
        ], dtype=np.float32)

        tform = cv2.estimateAffinePartial2D(landmarks, dst)[0]
        if tform is not None:
            aligned = cv2.warpAffine(image, tform, (112, 112))
            assert aligned.shape == (112, 112, 3)

    def test_embedding_normalization(self) -> None:
        """Test L2 normalization of face embeddings."""
        embedding = np.random.randn(512).astype(np.float32)
        normalized = embedding / np.linalg.norm(embedding)

        # Check unit length
        assert abs(np.linalg.norm(normalized) - 1.0) < 1e-6

    def test_cosine_similarity(self) -> None:
        """Test cosine similarity between embeddings."""
        emb1 = np.random.randn(512).astype(np.float32)
        emb1 = emb1 / np.linalg.norm(emb1)

        # Same embedding should have similarity ~1.0
        sim = float(np.dot(emb1, emb1))
        assert abs(sim - 1.0) < 1e-5

        # Different embeddings should have lower similarity
        emb2 = np.random.randn(512).astype(np.float32)
        emb2 = emb2 / np.linalg.norm(emb2)
        sim2 = float(np.dot(emb1, emb2))
        assert -1.0 <= sim2 <= 1.0

    def test_cosine_distance_threshold(self) -> None:
        """Test that cosine distance threshold separates matches from non-matches."""
        threshold = 0.4

        # Create a 'same person' scenario (small perturbation)
        base_emb = np.random.randn(512).astype(np.float32)
        base_emb = base_emb / np.linalg.norm(base_emb)

        noise = np.random.randn(512).astype(np.float32) * 0.1
        similar_emb = base_emb + noise
        similar_emb = similar_emb / np.linalg.norm(similar_emb)

        distance = 1.0 - float(np.dot(base_emb, similar_emb))
        # Small noise should result in small distance
        assert distance < threshold

    def test_image_quality_blur_detection(self) -> None:
        """Test blur detection via Laplacian variance."""
        import cv2

        # Sharp image (high variance)
        sharp = np.random.randint(0, 255, (112, 112), dtype=np.uint8)
        sharp_score = cv2.Laplacian(sharp, cv2.CV_64F).var()

        # Blurry image (low variance)
        blurry = cv2.GaussianBlur(sharp, (21, 21), 10)
        blurry_score = cv2.Laplacian(blurry, cv2.CV_64F).var()

        assert sharp_score > blurry_score


class TestImageUtils:
    """Test image utility functions."""

    def test_decode_encode_base64(self) -> None:
        """Test base64 image encode/decode roundtrip."""
        from app.utils.image_utils import decode_base64_image, encode_image_to_base64

        # Create a test image
        original = np.random.randint(0, 255, (100, 100, 3), dtype=np.uint8)

        # Encode to base64
        b64_str = encode_image_to_base64(original, format="png")
        assert isinstance(b64_str, str)
        assert len(b64_str) > 0

        # Decode back
        decoded = decode_base64_image(b64_str)
        assert decoded.shape[0] > 0
        assert decoded.shape[1] > 0

    def test_resize_image(self) -> None:
        """Test image resizing preserves aspect ratio."""
        from app.utils.image_utils import resize_image

        image = np.random.randint(0, 255, (1080, 1920, 3), dtype=np.uint8)
        resized = resize_image(image, max_size=640)

        assert max(resized.shape[:2]) <= 640
        # Check aspect ratio is approximately preserved
        orig_ratio = 1920 / 1080
        new_ratio = resized.shape[1] / resized.shape[0]
        assert abs(orig_ratio - new_ratio) < 0.1

    def test_crop_image(self) -> None:
        """Test image cropping."""
        from app.utils.image_utils import crop_image

        image = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
        cropped = crop_image(image, bbox=(100, 100, 300, 300))

        assert cropped.shape == (200, 200, 3)
