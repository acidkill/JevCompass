"""Public API for the fictional ParcelCache codec."""
# Seeded defect: the implementation moved from codec.py to wire.py.
from .codec import decode, encode

__all__ = ["decode", "encode"]
