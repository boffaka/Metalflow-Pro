"""GET /api/v1/equipment-catalog — static reference list (60 codes)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

try:
    from ..auth import current_user
    from ..equipment_catalog import EQUIPMENT_CATALOG, get_grouped_catalog
except ImportError:  # pragma: no cover
    from auth import current_user
    from equipment_catalog import EQUIPMENT_CATALOG, get_grouped_catalog


router = APIRouter(prefix="/api/v1", tags=["equipment-catalog"])


@router.get("/equipment-catalog")
def list_equipment_catalog(user=Depends(current_user)):
    """Return the equipment catalog grouped by category."""
    return {
        "total": len(EQUIPMENT_CATALOG),
        "groups": get_grouped_catalog(),
    }
