"""§4.6 step 4 support — KMeans clusters used as Thompson-sampling explore arms."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from core.config import N_CLUSTERS

logger = logging.getLogger(__name__)


def build_clusters(matrix: np.ndarray, product_ids: pd.Series) -> dict[str, int]:
    from sklearn.cluster import KMeans

    k = min(N_CLUSTERS, max(2, len(product_ids) // 10))
    labels = KMeans(n_clusters=k, n_init=10, random_state=42).fit_predict(matrix)
    logger.info("KMeans: %d clusters over %d products", k, len(product_ids))
    return {pid: int(lbl) for pid, lbl in zip(product_ids, labels)}
