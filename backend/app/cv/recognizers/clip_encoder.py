"""
CLIP (Contrastive Language-Image Pre-training) Encoder for VisionAI.

Provides image and text encoding using CLIP ViT-B/32 ONNX models for
natural language video search.  Encodes images and text into a shared
512-dimensional embedding space where cosine similarity measures
semantic relatedness.

The encoder supports:
- Single image encoding with CLIP-standard preprocessing
- Batch image encoding for efficient recording indexing
- Text encoding with a simplified BPE tokenizer
- Cosine similarity computation and top-k search

Usage::

    from app.cv.recognizers.clip_encoder import CLIPEncoder

    encoder = CLIPEncoder()
    image_emb = encoder.encode_image(frame)
    text_emb = encoder.encode_text("person wearing red jacket")
    score = encoder.compute_similarity(image_emb, text_emb)
"""

from __future__ import annotations

import hashlib
import html
import os
import re
import ftfy
import gzip
from functools import lru_cache
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import onnxruntime as ort
import structlog

logger = structlog.stdlib.get_logger(__name__)

# ── CLIP Image Preprocessing Constants ────────────────────────────────────────

CLIP_IMAGE_SIZE = 224
CLIP_EMBEDDING_DIM = 512

# CLIP normalization (ImageNet-based, per OpenAI CLIP)
CLIP_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
CLIP_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)

# ── BPE Tokenizer Constants ──────────────────────────────────────────────────

CONTEXT_LENGTH = 77
SOT_TOKEN = 49406
EOT_TOKEN = 49407
MAX_VOCAB_SIZE = 49408


def _bytes_to_unicode() -> dict[int, str]:
    """Build the byte-to-unicode mapping used by CLIP's BPE tokenizer.

    Returns a mapping from byte values (0-255) to unicode characters,
    matching the GPT-2 / CLIP byte-level BPE vocabulary.
    """
    bs = (
        list(range(ord("!"), ord("~") + 1))
        + list(range(ord("\xa1"), ord("\xac") + 1))
        + list(range(ord("\xae"), ord("\xff") + 1))
    )
    cs = bs[:]
    n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    cs_chars = [chr(c) for c in cs]
    return dict(zip(bs, cs_chars))


def _get_pairs(word: tuple[str, ...]) -> set[tuple[str, str]]:
    """Return the set of symbol bigrams in a word.

    Args:
        word: Tuple of symbols (characters or merged BPE tokens).

    Returns:
        Set of adjacent symbol pairs.
    """
    pairs: set[tuple[str, str]] = set()
    prev_char = word[0]
    for char in word[1:]:
        pairs.add((prev_char, char))
        prev_char = char
    return pairs


def _basic_clean(text: str) -> str:
    """Apply basic text cleaning: fix unicode, unescape HTML, strip whitespace."""
    text = ftfy.fix_text(text)
    text = html.unescape(html.unescape(text))
    return text.strip()


def _whitespace_clean(text: str) -> str:
    """Collapse multiple whitespace characters into single spaces."""
    text = re.sub(r"\s+", " ", text)
    return text.strip()


class SimpleTokenizer:
    """Simplified BPE tokenizer compatible with OpenAI CLIP.

    Implements byte-level BPE tokenization matching the CLIP model's
    vocabulary. Falls back to a basic whitespace tokenizer if the
    BPE vocabulary file is not available.

    Args:
        bpe_path: Optional path to the ``bpe_simple_vocab_16e6.txt.gz``
            vocabulary file.  If ``None``, attempts to find it in the
            model directory or falls back to basic tokenization.
    """

    def __init__(self, bpe_path: Optional[str] = None) -> None:
        self.byte_encoder = _bytes_to_unicode()
        self.byte_decoder = {v: k for k, v in self.byte_encoder.items()}
        self.bpe_ranks: dict[tuple[str, str], int] = {}
        self.encoder: dict[str, int] = {}
        self.decoder: dict[int, str] = {}
        self.cache: dict[str, str] = {
            "<|startoftext|>": "<|startoftext|>",
            "<|endoftext|>": "<|endoftext|>",
        }
        self.pat = re.compile(
            r"""<\|startoftext\|>|<\|endoftext\|>|'s|'t|'re|'ve|'m|'ll|'d|[\p{L}]+|[\p{N}]|[^\s\p{L}\p{N}]+""",
            re.IGNORECASE,
        )
        self._initialized = False

        if bpe_path:
            self._load_bpe(bpe_path)
        else:
            self._try_find_bpe()

    def _try_find_bpe(self) -> None:
        """Search standard locations for the BPE vocabulary file."""
        search_paths = [
            "/opt/visionai/models/bpe_simple_vocab_16e6.txt.gz",
            os.path.join(os.path.dirname(__file__), "bpe_simple_vocab_16e6.txt.gz"),
            os.path.expanduser("~/.cache/clip/bpe_simple_vocab_16e6.txt.gz"),
        ]
        from app.config import get_settings

        try:
            settings = get_settings()
            search_paths.insert(
                0,
                os.path.join(settings.MODEL_DIR, "bpe_simple_vocab_16e6.txt.gz"),
            )
        except Exception:
            pass

        for path in search_paths:
            if os.path.isfile(path):
                self._load_bpe(path)
                return

        logger.warning(
            "clip_encoder.bpe_vocab_not_found",
            searched=search_paths,
            message="Falling back to basic tokenization",
        )

    def _load_bpe(self, bpe_path: str) -> None:
        """Load BPE merges from a gzipped vocabulary file.

        Args:
            bpe_path: Path to the compressed BPE vocabulary file.
        """
        try:
            with gzip.open(bpe_path, "rt", encoding="utf-8") as f:
                bpe_data = f.read()

            merges = bpe_data.split("\n")[1 : 49152 - 256 - 2 + 1]
            merges = [tuple(merge.split()) for merge in merges]
            vocab = list(_bytes_to_unicode().values())
            vocab = vocab + [v + "</w>" for v in vocab]

            for merge in merges:
                vocab.append("".join(merge))

            vocab.extend(["<|startoftext|>", "<|endoftext|>"])

            self.encoder = {token: idx for idx, token in enumerate(vocab)}
            self.decoder = {idx: token for token, idx in self.encoder.items()}
            self.bpe_ranks = {pair: i for i, pair in enumerate(merges)}
            self._initialized = True

            logger.info(
                "clip_encoder.bpe_loaded",
                vocab_size=len(self.encoder),
                merges=len(self.bpe_ranks),
            )

        except Exception as exc:
            logger.error("clip_encoder.bpe_load_failed", error=str(exc))
            self._initialized = False

    def bpe(self, token: str) -> str:
        """Apply BPE encoding to a single token.

        Args:
            token: Token string to encode.

        Returns:
            Space-separated string of BPE tokens.
        """
        if token in self.cache:
            return self.cache[token]

        word = tuple(token[:-1]) + (token[-1] + "</w>",)
        pairs = _get_pairs(word)

        if not pairs:
            return token + "</w>"

        while True:
            bigram = min(pairs, key=lambda pair: self.bpe_ranks.get(pair, float("inf")))
            if bigram not in self.bpe_ranks:
                break

            first, second = bigram
            new_word: list[str] = []
            i = 0
            while i < len(word):
                try:
                    j = word.index(first, i)
                except ValueError:
                    new_word.extend(word[i:])
                    break
                new_word.extend(word[i:j])
                i = j

                if word[i] == first and i < len(word) - 1 and word[i + 1] == second:
                    new_word.append(first + second)
                    i += 2
                else:
                    new_word.append(word[i])
                    i += 1

            word = tuple(new_word)
            if len(word) == 1:
                break
            pairs = _get_pairs(word)

        result = " ".join(word)
        self.cache[token] = result
        return result

    def encode(self, text: str) -> list[int]:
        """Encode text into a list of BPE token IDs.

        Args:
            text: Input text to tokenize.

        Returns:
            List of integer token IDs.
        """
        if not self._initialized:
            return self._basic_encode(text)

        text = _whitespace_clean(_basic_clean(text)).lower()
        bpe_tokens: list[int] = []

        for token_match in re.findall(self.pat, text):
            token_bytes = token_match.encode("utf-8")
            token_translated = "".join(
                self.byte_encoder[b] for b in token_bytes
            )
            bpe_result = self.bpe(token_translated)
            for bpe_token in bpe_result.split(" "):
                if bpe_token in self.encoder:
                    bpe_tokens.append(self.encoder[bpe_token])

        return bpe_tokens

    def _basic_encode(self, text: str) -> list[int]:
        """Fallback tokenizer when BPE vocabulary is not available.

        Uses a simple hash-based encoding that maps words to token IDs
        within the vocabulary range. Not as accurate as full BPE but
        provides reasonable search functionality.

        Args:
            text: Input text to tokenize.

        Returns:
            List of pseudo token IDs.
        """
        text = text.lower().strip()
        words = re.split(r"\s+", text)
        tokens: list[int] = []
        for word in words:
            word_hash = int(hashlib.md5(word.encode()).hexdigest(), 16)
            token_id = (word_hash % (MAX_VOCAB_SIZE - 2)) + 1
            tokens.append(token_id)
        return tokens

    def tokenize(
        self,
        text: str,
        context_length: int = CONTEXT_LENGTH,
    ) -> np.ndarray:
        """Tokenize text and pad/truncate to context_length.

        Adds start-of-text and end-of-text tokens, pads with zeros,
        and returns an int32 array suitable for model input.

        Args:
            text: Input text string.
            context_length: Maximum sequence length (default 77).

        Returns:
            np.ndarray: Integer token array of shape ``(context_length,)``.
        """
        tokens = self.encode(text)
        result = np.zeros(context_length, dtype=np.int32)
        result[0] = SOT_TOKEN

        # Truncate if too long (leaving room for SOT and EOT)
        max_text_tokens = context_length - 2
        tokens = tokens[:max_text_tokens]

        for i, tok in enumerate(tokens):
            result[i + 1] = tok

        eot_pos = min(len(tokens) + 1, context_length - 1)
        result[eot_pos] = EOT_TOKEN

        return result


# ── Module-level tokenizer singleton ──────────────────────────────────────

_tokenizer: SimpleTokenizer | None = None


def _get_tokenizer() -> SimpleTokenizer:
    """Return (and lazily create) the module-level tokenizer."""
    global _tokenizer
    if _tokenizer is None:
        _tokenizer = SimpleTokenizer()
    return _tokenizer


class CLIPEncoder:
    """CLIP ViT-B/32 encoder for image and text embedding.

    Loads CLIP visual and text encoder ONNX models via the ModelRegistry
    and provides methods for encoding images and text into a shared
    512-dimensional embedding space.

    The encoder performs:
    - Image preprocessing: resize to 224x224, normalize with CLIP means/stds,
      convert to NCHW float32 format
    - Text tokenization: BPE tokenization compatible with OpenAI CLIP
    - L2 normalization of all embeddings for cosine similarity computation

    Args:
        visual_session: ONNX Runtime session for the CLIP visual encoder.
            If ``None``, loads from ModelRegistry.
        text_session: ONNX Runtime session for the CLIP text encoder.
            If ``None``, loads from ModelRegistry.
    """

    def __init__(
        self,
        visual_session: Optional[ort.InferenceSession] = None,
        text_session: Optional[ort.InferenceSession] = None,
    ) -> None:
        self._visual_session = visual_session
        self._text_session = text_session
        self._tokenizer = _get_tokenizer()
        self._visual_input_name: Optional[str] = None
        self._text_input_name: Optional[str] = None

        if visual_session is None or text_session is None:
            self._load_from_registry()

        # Cache input/output names for performance
        if self._visual_session is not None:
            inputs = self._visual_session.get_inputs()
            self._visual_input_name = inputs[0].name if inputs else "pixel_values"
            outputs = self._visual_session.get_outputs()
            self._visual_output_name = outputs[0].name if outputs else "image_features"

        if self._text_session is not None:
            inputs = self._text_session.get_inputs()
            self._text_input_name = inputs[0].name if inputs else "input_ids"
            outputs = self._text_session.get_outputs()
            self._text_output_name = outputs[0].name if outputs else "text_features"

        logger.info(
            "clip_encoder.initialised",
            visual_loaded=self._visual_session is not None,
            text_loaded=self._text_session is not None,
            embedding_dim=CLIP_EMBEDDING_DIM,
        )

    def _load_from_registry(self) -> None:
        """Attempt to load CLIP models from the ModelRegistry.

        Tries the registry first, then falls back to loading directly
        from the model directory.
        """
        from app.cv.model_registry import ModelRegistry
        from app.config import get_settings

        registry = ModelRegistry()
        settings = get_settings()
        model_dir = Path(settings.MODEL_DIR)

        # Visual encoder
        if self._visual_session is None:
            try:
                self._visual_session = registry.get_model("clip_visual")
            except KeyError:
                visual_path = model_dir / "clip_visual.onnx"
                if visual_path.exists():
                    self._visual_session = registry.load_model(
                        "clip_visual", visual_path
                    )
                else:
                    logger.warning(
                        "clip_encoder.visual_model_not_found",
                        path=str(visual_path),
                    )

        # Text encoder
        if self._text_session is None:
            try:
                self._text_session = registry.get_model("clip_text")
            except KeyError:
                text_path = model_dir / "clip_text.onnx"
                if text_path.exists():
                    self._text_session = registry.load_model(
                        "clip_text", text_path
                    )
                else:
                    logger.warning(
                        "clip_encoder.text_model_not_found",
                        path=str(text_path),
                    )

    # ── Image Encoding ────────────────────────────────────────────────────

    @staticmethod
    def preprocess_image(image: np.ndarray) -> np.ndarray:
        """Preprocess an image for CLIP visual encoder input.

        Applies the standard CLIP preprocessing pipeline:
        1. Resize to 224x224 using bicubic interpolation
        2. Convert BGR to RGB
        3. Scale pixel values to [0, 1]
        4. Normalize with CLIP means and standard deviations
        5. Transpose to NCHW format

        Args:
            image: Input BGR image as a NumPy array of shape ``(H, W, 3)``.

        Returns:
            np.ndarray: Preprocessed float32 tensor of shape ``(1, 3, 224, 224)``.
        """
        # Resize to 224x224 with bicubic interpolation
        resized = cv2.resize(
            image,
            (CLIP_IMAGE_SIZE, CLIP_IMAGE_SIZE),
            interpolation=cv2.INTER_CUBIC,
        )

        # BGR to RGB
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)

        # Scale to [0, 1] float32
        normalized = rgb.astype(np.float32) / 255.0

        # Normalize with CLIP means and stds
        normalized = (normalized - CLIP_MEAN) / CLIP_STD

        # HWC to CHW
        transposed = normalized.transpose(2, 0, 1)

        # Add batch dimension: (1, 3, 224, 224)
        batched = np.expand_dims(transposed, axis=0).astype(np.float32)

        return batched

    @staticmethod
    def _l2_normalize(embedding: np.ndarray) -> np.ndarray:
        """L2-normalize an embedding vector or batch of vectors.

        Args:
            embedding: Array of shape ``(D,)`` or ``(N, D)``.

        Returns:
            np.ndarray: L2-normalized embedding(s) of the same shape.
        """
        if embedding.ndim == 1:
            norm = np.linalg.norm(embedding)
            if norm > 0:
                return embedding / norm
            return embedding
        else:
            norms = np.linalg.norm(embedding, axis=1, keepdims=True)
            norms = np.maximum(norms, 1e-8)
            return embedding / norms

    def encode_image(self, image: np.ndarray) -> np.ndarray:
        """Encode a single image into a 512-dimensional CLIP embedding.

        Preprocesses the image (resize 224x224, normalize with CLIP
        means/stds, NCHW format), runs ONNX inference on the visual
        encoder, and L2-normalizes the output.

        Args:
            image: Input BGR image as a NumPy array of shape ``(H, W, 3)``.

        Returns:
            np.ndarray: L2-normalized embedding of shape ``(512,)``.

        Raises:
            RuntimeError: If the visual encoder model is not loaded.
        """
        if self._visual_session is None:
            raise RuntimeError(
                "CLIP visual encoder is not loaded. "
                "Ensure 'clip_visual.onnx' is available in the model directory."
            )

        # Preprocess
        input_tensor = self.preprocess_image(image)

        # Run inference
        output = self._visual_session.run(
            None,
            {self._visual_input_name: input_tensor},
        )

        # Extract embedding and normalize
        embedding = output[0].flatten().astype(np.float32)

        # Ensure correct dimensionality
        if embedding.shape[0] != CLIP_EMBEDDING_DIM:
            embedding = embedding[:CLIP_EMBEDDING_DIM]

        return self._l2_normalize(embedding)

    def encode_images_batch(
        self,
        images: list[np.ndarray],
        batch_size: int = 8,
    ) -> np.ndarray:
        """Encode a batch of images into CLIP embeddings.

        Processes images in sub-batches for memory efficiency.

        Args:
            images: List of BGR images as NumPy arrays.
            batch_size: Maximum number of images per inference batch.

        Returns:
            np.ndarray: L2-normalized embeddings of shape ``(N, 512)``.

        Raises:
            RuntimeError: If the visual encoder model is not loaded.
        """
        if self._visual_session is None:
            raise RuntimeError(
                "CLIP visual encoder is not loaded. "
                "Ensure 'clip_visual.onnx' is available in the model directory."
            )

        if not images:
            return np.empty((0, CLIP_EMBEDDING_DIM), dtype=np.float32)

        all_embeddings: list[np.ndarray] = []

        for i in range(0, len(images), batch_size):
            batch = images[i : i + batch_size]

            # Preprocess each image and stack into a batch
            preprocessed = [self.preprocess_image(img) for img in batch]
            batch_tensor = np.concatenate(preprocessed, axis=0)

            # Run batch inference
            output = self._visual_session.run(
                None,
                {self._visual_input_name: batch_tensor},
            )

            batch_embeddings = output[0].astype(np.float32)

            # Handle shape: might be (N, D) or (N, 1, D)
            if batch_embeddings.ndim == 3:
                batch_embeddings = batch_embeddings.squeeze(1)

            # Ensure correct dimensionality
            if batch_embeddings.shape[-1] != CLIP_EMBEDDING_DIM:
                batch_embeddings = batch_embeddings[:, :CLIP_EMBEDDING_DIM]

            all_embeddings.append(batch_embeddings)

        embeddings = np.concatenate(all_embeddings, axis=0)
        return self._l2_normalize(embeddings)

    # ── Text Encoding ─────────────────────────────────────────────────────

    def encode_text(self, text: str) -> np.ndarray:
        """Encode a text string into a 512-dimensional CLIP embedding.

        Tokenizes the text using the CLIP BPE tokenizer, runs ONNX
        inference on the text encoder, and L2-normalizes the output.

        Args:
            text: Input text string (e.g. "person wearing red jacket").

        Returns:
            np.ndarray: L2-normalized embedding of shape ``(512,)``.

        Raises:
            RuntimeError: If the text encoder model is not loaded.
        """
        if self._text_session is None:
            raise RuntimeError(
                "CLIP text encoder is not loaded. "
                "Ensure 'clip_text.onnx' is available in the model directory."
            )

        # Tokenize
        tokens = self._tokenizer.tokenize(text)
        token_tensor = np.expand_dims(tokens, axis=0).astype(np.int32)

        # Run inference
        output = self._text_session.run(
            None,
            {self._text_input_name: token_tensor},
        )

        # Extract embedding and normalize
        embedding = output[0].flatten().astype(np.float32)

        # Ensure correct dimensionality
        if embedding.shape[0] != CLIP_EMBEDDING_DIM:
            embedding = embedding[:CLIP_EMBEDDING_DIM]

        return self._l2_normalize(embedding)

    def encode_texts_batch(self, texts: list[str]) -> np.ndarray:
        """Encode multiple text strings into CLIP embeddings.

        Args:
            texts: List of input text strings.

        Returns:
            np.ndarray: L2-normalized embeddings of shape ``(N, 512)``.

        Raises:
            RuntimeError: If the text encoder model is not loaded.
        """
        if self._text_session is None:
            raise RuntimeError(
                "CLIP text encoder is not loaded. "
                "Ensure 'clip_text.onnx' is available in the model directory."
            )

        if not texts:
            return np.empty((0, CLIP_EMBEDDING_DIM), dtype=np.float32)

        # Tokenize all texts
        token_batch = np.stack(
            [self._tokenizer.tokenize(text) for text in texts],
            axis=0,
        ).astype(np.int32)

        # Run batch inference
        output = self._text_session.run(
            None,
            {self._text_input_name: token_batch},
        )

        embeddings = output[0].astype(np.float32)

        if embeddings.ndim == 3:
            embeddings = embeddings.squeeze(1)

        if embeddings.shape[-1] != CLIP_EMBEDDING_DIM:
            embeddings = embeddings[:, :CLIP_EMBEDDING_DIM]

        return self._l2_normalize(embeddings)

    # ── Similarity Computation ────────────────────────────────────────────

    @staticmethod
    def compute_similarity(
        image_embedding: np.ndarray,
        text_embedding: np.ndarray,
    ) -> float:
        """Compute cosine similarity between an image and text embedding.

        Both embeddings should be L2-normalized, in which case the
        cosine similarity equals the dot product.

        Args:
            image_embedding: Image embedding of shape ``(512,)``.
            text_embedding: Text embedding of shape ``(512,)``.

        Returns:
            float: Cosine similarity score in ``[-1, 1]``.
        """
        return float(np.dot(image_embedding, text_embedding))

    @staticmethod
    def compute_similarity_batch(
        image_embeddings: np.ndarray,
        text_embedding: np.ndarray,
    ) -> np.ndarray:
        """Compute cosine similarities between multiple images and a text.

        Args:
            image_embeddings: Image embeddings of shape ``(N, 512)``.
            text_embedding: Text embedding of shape ``(512,)``.

        Returns:
            np.ndarray: Similarity scores of shape ``(N,)``.
        """
        return image_embeddings @ text_embedding

    def search_by_text(
        self,
        text: str,
        image_embeddings: np.ndarray,
        top_k: int = 10,
    ) -> list[tuple[int, float]]:
        """Search image embeddings using a natural language text query.

        Encodes the query text, computes cosine similarity against all
        image embeddings, and returns the top-k most similar results
        sorted by descending similarity.

        Args:
            text: Natural language search query.
            image_embeddings: Matrix of image embeddings, shape ``(N, 512)``.
            top_k: Number of top results to return.

        Returns:
            List of ``(index, similarity_score)`` tuples sorted by
            descending similarity.
        """
        text_embedding = self.encode_text(text)

        # Compute similarities
        similarities = self.compute_similarity_batch(
            image_embeddings, text_embedding
        )

        # Get top-k indices
        if top_k >= len(similarities):
            top_indices = np.argsort(similarities)[::-1]
        else:
            # Use argpartition for efficiency on large arrays
            top_indices = np.argpartition(similarities, -top_k)[-top_k:]
            top_indices = top_indices[np.argsort(similarities[top_indices])[::-1]]

        results = [
            (int(idx), float(similarities[idx]))
            for idx in top_indices
        ]

        return results

    def search_by_image(
        self,
        query_image: np.ndarray,
        image_embeddings: np.ndarray,
        top_k: int = 10,
    ) -> list[tuple[int, float]]:
        """Search image embeddings using a query image.

        Encodes the query image, computes cosine similarity against all
        stored embeddings, and returns the top-k most similar results.

        Args:
            query_image: BGR query image as NumPy array.
            image_embeddings: Matrix of image embeddings, shape ``(N, 512)``.
            top_k: Number of top results to return.

        Returns:
            List of ``(index, similarity_score)`` tuples sorted by
            descending similarity.
        """
        query_embedding = self.encode_image(query_image)
        similarities = self.compute_similarity_batch(
            image_embeddings, query_embedding
        )

        if top_k >= len(similarities):
            top_indices = np.argsort(similarities)[::-1]
        else:
            top_indices = np.argpartition(similarities, -top_k)[-top_k:]
            top_indices = top_indices[np.argsort(similarities[top_indices])[::-1]]

        return [
            (int(idx), float(similarities[idx]))
            for idx in top_indices
        ]


# ── Module-level encoder singleton ────────────────────────────────────────

_encoder_instance: CLIPEncoder | None = None


def get_clip_encoder() -> CLIPEncoder:
    """Return (and lazily create) the module-level CLIPEncoder singleton.

    Returns:
        CLIPEncoder: The shared encoder instance.
    """
    global _encoder_instance
    if _encoder_instance is None:
        _encoder_instance = CLIPEncoder()
    return _encoder_instance
