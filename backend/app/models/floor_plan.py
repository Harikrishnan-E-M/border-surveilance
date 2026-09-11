"""Floor plan, camera placement, zone placement, and overlay models.

Supports digital twin / floor plan integration: upload floor plan images,
place cameras and zones on the map, generate heatmap overlays, and
track live occupancy and person movement across the spatial layout.
"""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy import JSON as JSONB, Uuid as UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.camera import Camera
    from app.models.organization import Organization
    from app.models.zone import Zone


class OverlayType(str, enum.Enum):
    """Types of generated overlays that can be projected onto a floor plan."""

    HEATMAP = "heatmap"
    OCCUPANCY = "occupancy"
    ALERTS = "alerts"
    TRACKS = "tracks"


class FloorPlan(Base):
    """A floor plan image representing a physical building floor.

    Floor plans serve as the spatial canvas for the digital twin view.
    Cameras and zones are placed onto the floor plan using relative
    coordinates (0-1), and analytics overlays (heatmaps, occupancy,
    alerts, tracks) are generated and projected onto the image.

    The ``scale_meters_per_pixel`` factor allows converting pixel
    distances to real-world meters for accurate spatial analytics.
    """

    __tablename__ = "floor_plans"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(255), nullable=False,
    )
    building_name: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True,
    )
    floor_number: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0",
    )
    image_path: Mapped[str] = mapped_column(
        String(1024), nullable=False,
        comment="MinIO object path to the uploaded floor plan image",
    )
    width_px: Mapped[int] = mapped_column(
        Integer, nullable=False,
        comment="Original image width in pixels",
    )
    height_px: Mapped[int] = mapped_column(
        Integer, nullable=False,
        comment="Original image height in pixels",
    )
    scale_meters_per_pixel: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, default=None,
        comment="Conversion factor from pixels to real-world meters",
    )
    metadata_json: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True, default=dict,
        comment="Arbitrary metadata (building info, annotations, etc.)",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true",
    )

    # -- Relationships ---------------------------------------------------------
    organization: Mapped["Organization"] = relationship(
        "Organization", lazy="selectin",
    )
    camera_placements: Mapped[List["CameraPlacement"]] = relationship(
        "CameraPlacement",
        back_populates="floor_plan",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    zone_placements: Mapped[List["ZonePlacement"]] = relationship(
        "ZonePlacement",
        back_populates="floor_plan",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    overlays: Mapped[List["FloorPlanOverlay"]] = relationship(
        "FloorPlanOverlay",
        back_populates="floor_plan",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<FloorPlan(id={self.id}, name='{self.name}', "
            f"building='{self.building_name}', floor={self.floor_number})>"
        )


class CameraPlacement(Base):
    """Position and orientation of a camera on a floor plan.

    Coordinates are stored as relative values (0.0 to 1.0) so they
    remain valid regardless of the display resolution.  The field of
    view (FOV) is rendered as a semi-transparent cone emanating from
    the camera icon.
    """

    __tablename__ = "camera_placements"

    floor_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("floor_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    camera_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cameras.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    x_position: Mapped[float] = mapped_column(
        Float, nullable=False,
        comment="Horizontal position as fraction of image width (0.0-1.0)",
    )
    y_position: Mapped[float] = mapped_column(
        Float, nullable=False,
        comment="Vertical position as fraction of image height (0.0-1.0)",
    )
    rotation_degrees: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0",
        comment="Camera rotation in degrees (0-360), 0 = pointing right",
    )
    fov_angle: Mapped[float] = mapped_column(
        Float, nullable=False, default=90.0, server_default="90",
        comment="Field of view cone angle in degrees",
    )
    fov_range: Mapped[float] = mapped_column(
        Float, nullable=False, default=100.0, server_default="100",
        comment="FOV cone range in pixels on the map",
    )
    label: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True,
        comment="Display label override (defaults to camera name)",
    )

    # -- Relationships ---------------------------------------------------------
    floor_plan: Mapped["FloorPlan"] = relationship(
        "FloorPlan", back_populates="camera_placements", lazy="selectin",
    )
    camera: Mapped["Camera"] = relationship(
        "Camera", lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<CameraPlacement(id={self.id}, floor_plan_id={self.floor_plan_id}, "
            f"camera_id={self.camera_id}, pos=({self.x_position:.2f}, {self.y_position:.2f}))>"
        )


class ZonePlacement(Base):
    """A zone polygon drawn on a floor plan.

    The polygon is defined by an array of relative coordinate points
    (each with ``x`` and ``y`` between 0.0 and 1.0).  Color and opacity
    control how the zone is rendered on the interactive floor plan viewer.
    """

    __tablename__ = "zone_placements"

    floor_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("floor_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    zone_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("zones.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    polygon_points: Mapped[list] = mapped_column(
        JSONB, nullable=False,
        comment="Array of {x, y} relative coordinate objects defining the polygon",
    )
    color: Mapped[str] = mapped_column(
        String(9), nullable=False, default="#3B82F6", server_default="#3B82F6",
        comment="Hex colour for the zone polygon fill",
    )
    opacity: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.3, server_default="0.3",
        comment="Fill opacity (0.0 transparent - 1.0 opaque)",
    )

    # -- Relationships ---------------------------------------------------------
    floor_plan: Mapped["FloorPlan"] = relationship(
        "FloorPlan", back_populates="zone_placements", lazy="selectin",
    )
    zone: Mapped["Zone"] = relationship(
        "Zone", lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<ZonePlacement(id={self.id}, floor_plan_id={self.floor_plan_id}, "
            f"zone_id={self.zone_id}, points={len(self.polygon_points or [])})>"
        )


class FloorPlanOverlay(Base):
    """A generated overlay image projected onto a floor plan.

    Overlays are time-bounded artifacts (heatmaps, occupancy snapshots,
    alert layers, person track visualisations) stored in MinIO and
    composited on the floor plan in the frontend viewer.
    """

    __tablename__ = "floor_plan_overlays"

    floor_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("floor_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    overlay_type: Mapped[OverlayType] = mapped_column(
        String(32),
        nullable=False,
    )
    data_url: Mapped[str] = mapped_column(
        String(1024), nullable=False,
        comment="MinIO path to the generated overlay image",
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    valid_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="Expiry time after which the overlay should be regenerated",
    )

    # -- Relationships ---------------------------------------------------------
    floor_plan: Mapped["FloorPlan"] = relationship(
        "FloorPlan", back_populates="overlays", lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<FloorPlanOverlay(id={self.id}, floor_plan_id={self.floor_plan_id}, "
            f"type={self.overlay_type.value}, generated_at={self.generated_at})>"
        )
