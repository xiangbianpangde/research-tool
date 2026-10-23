"""Evidence-pinned observations and reviewed dispositions; no clinical truth certification."""

from .records import DispositionError, digest
from .registry import Registry

__all__ = ["DispositionError", "Registry", "digest"]
