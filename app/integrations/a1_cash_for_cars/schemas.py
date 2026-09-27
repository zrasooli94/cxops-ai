from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


def _current_year_plus_one() -> int:
    return datetime.now(UTC).date().year + 1


VehicleCondition = Literal["excellent", "good", "fair", "poor"]

PickupWindow = Literal["morning", "afternoon", "evening"]


class VehicleLead(BaseModel):
    vehicle_make: str = Field(min_length=1, max_length=60)
    vehicle_model: str = Field(min_length=1, max_length=60)
    vehicle_year: int = Field(ge=1900, le=_current_year_plus_one())
    vehicle_variant: str | None = Field(default=None, max_length=60)
    odometer_km: int | None = Field(default=None, ge=0, le=2_000_000)
    condition: VehicleCondition
    license_plate: str | None = Field(default=None, max_length=20)
    phone_contact: str | None = Field(default=None, max_length=30)
    notes: str | None = Field(default=None, max_length=500)


class VehicleLeadUpdate(BaseModel):
    reference_id: str = Field(min_length=1, max_length=64)
    vehicle_make: str | None = Field(default=None, min_length=1, max_length=60)
    vehicle_model: str | None = Field(default=None, min_length=1, max_length=60)
    vehicle_year: int | None = Field(default=None, ge=1900, le=_current_year_plus_one())
    vehicle_variant: str | None = Field(default=None, max_length=60)
    odometer_km: int | None = Field(default=None, ge=0, le=2_000_000)
    condition: VehicleCondition | None = None
    license_plate: str | None = Field(default=None, max_length=20)
    phone_contact: str | None = Field(default=None, max_length=30)
    notes: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "VehicleLeadUpdate":
        provided = 0
        for name, value in list(self.model_dump().items()):
            if name == "reference_id":
                continue
            if value is not None:
                provided += 1
        if provided == 0:
            raise ValueError("At least one vehicle detail field must be provided.")
        return self


class VehiclePhotosRequest(BaseModel):
    reference_id: str = Field(min_length=1, max_length=64)


class QuoteStatusRequest(BaseModel):
    reference_id: str = Field(min_length=1, max_length=64)


class PickupAvailabilityRequest(BaseModel):
    preferred_date: date
    preferred_window: PickupWindow | None = None


class PickupRequest(BaseModel):
    reference_id: str = Field(min_length=1, max_length=64)
    preferred_date: date
    preferred_window: PickupWindow
    pickup_address: str = Field(min_length=1, max_length=200)
    recipient_name: str | None = Field(default=None, max_length=100)