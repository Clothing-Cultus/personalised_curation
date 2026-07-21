"""Embedding backends shared by embed_images / embed_text and the CV job.

  "clip"          — open_clip ViT-B/32 (free, local, CPU-fine). Production.
  "hash_fallback" — deterministic pseudo-embeddings from a SHA256 of the text.
                    Geometry is meaningless but shapes/dtypes are identical, so
                    the whole pipeline + API runs on a machine without torch.

(Internal helper module — not in the spec §2.2 file list, but a plain shared
factory so embed_images and embed_text don't each re-load the CLIP model.)
"""
from __future__ import annotations

import hashlib
import io
import logging

import numpy as np

from core.config import (
    CLIP_MODEL,
    CLIP_PRETRAINED,
    EMBEDDING_BACKEND,
    EMBEDDING_DEVICE,
    EMBEDDING_DIM,
)

logger = logging.getLogger(__name__)


class ClipBackend:
    """open_clip ViT-B/32 — lazily imported so the API box never needs torch."""

    def __init__(self) -> None:
        import open_clip
        import torch

        self._torch = torch
        self.device = EMBEDDING_DEVICE
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            CLIP_MODEL, pretrained=CLIP_PRETRAINED, device=self.device
        )
        self.tokenizer = open_clip.get_tokenizer(CLIP_MODEL)
        self.model.eval()
        self.dim = EMBEDDING_DIM

    def embed_image(self, image_bytes: bytes) -> np.ndarray | None:
        from PIL import Image

        try:
            img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        except Exception:  # noqa: BLE001 — corrupt image: skip, don't kill the run
            return None
        with self._torch.no_grad():
            tensor = self.preprocess(img).unsqueeze(0).to(self.device)
            feat = self.model.encode_image(tensor)
            feat = feat / feat.norm(dim=-1, keepdim=True)
        return feat.squeeze(0).cpu().numpy().astype(np.float32)

    def embed_text(self, text: str) -> np.ndarray:
        with self._torch.no_grad():
            tokens = self.tokenizer([text[:300]]).to(self.device)
            feat = self.model.encode_text(tokens)
            feat = feat / feat.norm(dim=-1, keepdim=True)
        return feat.squeeze(0).cpu().numpy().astype(np.float32)


class HashFallbackBackend:
    """Deterministic pseudo-embeddings — dev/testing only."""

    def __init__(self) -> None:
        self.dim = EMBEDDING_DIM

    def _vec(self, seed_text: str) -> np.ndarray:
        seed = int.from_bytes(hashlib.sha256(seed_text.encode()).digest()[:8], "big")
        rng = np.random.default_rng(seed)
        v = rng.standard_normal(self.dim).astype(np.float32)
        return v / np.linalg.norm(v)

    def embed_image(self, image_bytes: bytes) -> np.ndarray:
        return self._vec(hashlib.sha256(image_bytes).hexdigest())

    def embed_text(self, text: str) -> np.ndarray:
        return self._vec(text)


def get_backend():
    if EMBEDDING_BACKEND == "clip":
        return ClipBackend()
    if EMBEDDING_BACKEND == "hash_fallback":
        logger.warning("Using hash_fallback embeddings — dev mode, NOT for production")
        return HashFallbackBackend()
    raise ValueError(f"Unknown embedding backend: {EMBEDDING_BACKEND}")
