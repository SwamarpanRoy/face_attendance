"""5-point similarity-transform alignment to the standard ArcFace 112x112 template.

This is a numpy port of ``insightface.utils.face_align`` (which uses scikit-image's
Umeyama estimator). It must stay numerically identical to the reference, because the
recogniser was trained on crops produced exactly this way and the templates stored on
the server were computed with it; a different alignment silently lowers match scores.
"""

from __future__ import annotations

import cv2
import numpy as np

from common.face.types import Array, FloatArray

#: ArcFace reference landmarks for a 112x112 crop (left eye, right eye, nose, mouth L/R).
ARCFACE_DST: FloatArray = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)
CROP_SIZE = 112


def umeyama(src: Array, dst: Array, *, estimate_scale: bool = True) -> Array:
    """Least-squares similarity transform mapping ``src`` points onto ``dst``.

    Umeyama (1991), transcribed from ``skimage.transform._geometric._umeyama`` so the
    result matches the reference bit-for-bit in the non-degenerate case. Returns a
    ``(dim+1, dim+1)`` homogeneous matrix.
    """
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    num, dim = src.shape

    src_mean = src.mean(axis=0)
    dst_mean = dst.mean(axis=0)
    src_demean = src - src_mean
    dst_demean = dst - dst_mean

    a = dst_demean.T @ src_demean / num
    d = np.ones((dim,), dtype=np.float64)
    if np.linalg.det(a) < 0:
        d[dim - 1] = -1

    t = np.eye(dim + 1, dtype=np.float64)
    u, s, v = np.linalg.svd(a)
    rank = np.linalg.matrix_rank(a)
    if rank == 0:
        return np.full_like(t, np.nan)
    if rank == dim - 1:
        if np.linalg.det(u) * np.linalg.det(v) > 0:
            t[:dim, :dim] = u @ v
        else:
            s_last = d[dim - 1]
            d[dim - 1] = -1
            t[:dim, :dim] = u @ np.diag(d) @ v
            d[dim - 1] = s_last
    else:
        t[:dim, :dim] = u @ np.diag(d) @ v

    scale = 1.0 / src_demean.var(axis=0).sum() * (s @ d) if estimate_scale else 1.0
    t[:dim, dim] = dst_mean - scale * (t[:dim, :dim] @ src_mean.T)
    t[:dim, :dim] *= scale
    return t


def estimate_norm(kps: Array, image_size: int = CROP_SIZE) -> FloatArray:
    """2x3 affine matrix that maps the 5 detected landmarks onto the ArcFace template."""
    if kps.shape != (5, 2):
        raise ValueError(f"expected 5x2 landmarks, got {kps.shape}")
    if image_size % 112 != 0:
        raise ValueError("image_size must be a multiple of 112 (ArcFace template)")
    dst = ARCFACE_DST * (image_size / 112.0)
    matrix = umeyama(kps, dst, estimate_scale=True)[0:2, :]
    return matrix.astype(np.float32)


def norm_crop(image_bgr: Array, kps: Array, image_size: int = CROP_SIZE) -> Array:
    """Warp the face so its landmarks land on the template; returns a square BGR crop."""
    matrix = estimate_norm(kps, image_size)
    warped = cv2.warpAffine(
        image_bgr, matrix, (image_size, image_size), borderValue=(0.0, 0.0, 0.0)
    )
    return np.asarray(warped)
