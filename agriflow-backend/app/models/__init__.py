"""
Importing this package registers every ORM model against
app.core.database.Base.metadata — required for Alembic autogenerate and
for `Base.metadata.create_all()` in tests/scripts.
"""
from app.models.crop import Crop, FarmVisit  # noqa: F401
from app.models.grain import CropInspection, GrainSale  # noqa: F401
from app.models.ledger import (  # noqa: F401
    AuditLog,
    BankChangeRequest,
    MarketRate,
    Notification,
    Transaction,
)
from app.models.seed import Seed, SeedPurchase, SeedWarehouse  # noqa: F401
from app.models.user import (  # noqa: F401
    FarmerDocument,
    FarmerProfile,
    FcmDeviceToken,
    RefreshToken,
    StaffProfile,
    User,
)
from app.models.warehouse import BookingSlot, Warehouse, WarehouseInventory, WarehouseSlot  # noqa: F401

__all__ = [
    "User",
    "FarmerProfile",
    "StaffProfile",
    "RefreshToken",
    "FarmerDocument",
    "FcmDeviceToken",
    "Crop",
    "FarmVisit",
    "Seed",
    "SeedWarehouse",
    "SeedPurchase",
    "Warehouse",
    "WarehouseSlot",
    "WarehouseInventory",
    "BookingSlot",
    "GrainSale",
    "CropInspection",
    "Transaction",
    "BankChangeRequest",
    "Notification",
    "AuditLog",
    "MarketRate",
]
