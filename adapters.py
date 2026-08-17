"""Compatibility shim.

Re-exports the public API previously available from ``adapters``. The real code
now lives in ``data`` (hydrant database) and ``routing`` (Manhattan distance).
"""

from data import DATA_PATH, get_hydrants
from routing.manhattan import nearby_hydrants

__all__ = ["DATA_PATH", "get_hydrants", "nearby_hydrants"]
