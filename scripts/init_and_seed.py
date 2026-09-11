"""Initialize database tables and seed default users."""

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.database import Base, engine
from app.models.organization import Organization, SubscriptionTier
from app.models.user import User, UserRole
import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


async def init_and_seed():
    print("=== Creating Database Tables ===")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Tables created successfully.")

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        async with session.begin():
            # Check or create default org
            result = await session.execute(
                select(Organization).where(Organization.slug == "visionai-demo")
            )
            org = result.scalar_one_or_none()

            if org is None:
                org = Organization(
                    id=uuid.uuid4(),
                    name="VisionAI Demo",
                    slug="visionai-demo",
                    subscription_tier=SubscriptionTier.ENTERPRISE.value,
                    max_cameras=100,
                    max_users=50,
                    timezone="Asia/Kolkata",
                    is_active=True,
                )
                session.add(org)
                await session.flush()
                print(f"Created Organization: {org.name}")
            else:
                print(f"Organization exists: {org.name}")

            # Admin user
            admin_email = "admin@visionai.com"
            admin_pass = "admin123456"

            result = await session.execute(select(User).where(User.email == admin_email))
            admin = result.scalar_one_or_none()

            if admin is None:
                admin = User(
                    id=uuid.uuid4(),
                    org_id=org.id,
                    email=admin_email,
                    hashed_password=hash_password(admin_pass),
                    full_name="System Administrator",
                    role=UserRole.SUPER_ADMIN,
                    phone="+919999999999",
                    is_active=True,
                    last_login=datetime.now(timezone.utc).replace(tzinfo=None),
                )
                session.add(admin)
                print(f"Created Admin User: {admin_email} / {admin_pass}")
            else:
                print(f"Admin User exists: {admin_email}")

            # Additional Users
            sample_users = [
                ("manager@visionai.com", "password123", "Site Manager", UserRole.ORG_ADMIN),
                ("operator@visionai.com", "password123", "Security Operator", UserRole.OPERATOR),
                ("viewer@visionai.com", "password123", "Dashboard Viewer", UserRole.VIEWER),
            ]

            for email, pwd, name, role in sample_users:
                res = await session.execute(select(User).where(User.email == email))
                if res.scalar_one_or_none() is None:
                    u = User(
                        id=uuid.uuid4(),
                        org_id=org.id,
                        email=email,
                        hashed_password=hash_password(pwd),
                        full_name=name,
                        role=role,
                        is_active=True,
                    )
                    session.add(u)
                    print(f"Created User: {email} / {pwd}")

            # Default Laptop Webcam Camera
            from app.models.camera import Camera, StreamProtocol, RecordingMode
            res_cam = await session.execute(select(Camera).where(Camera.name == "Laptop Integrated Webcam"))
            cam = res_cam.scalar_one_or_none()
            if cam is None:
                cam = Camera(
                    id=uuid.uuid4(),
                    org_id=org.id,
                    name="Laptop Integrated Webcam",
                    location_description="Built-in Laptop Web Camera",
                    stream_url="0",
                    protocol=StreamProtocol.USB,
                    resolution="1280x720",
                    fps=30,
                    codec="h264",
                    is_active=True,
                    is_online=True,
                    recording_mode=RecordingMode.EVENT,
                )
                session.add(cam)
                print(f"Created Camera: {cam.name} (stream_url: 0)")

    print("\nInitialization Complete!")
    print("=" * 50)
    print("Default Login Credentials:")
    print(f"  Admin:    {admin_email} / {admin_pass}")
    print("  Manager:  manager@visionai.com / password123")
    print("  Operator: operator@visionai.com / password123")
    print("  Viewer:   viewer@visionai.com / password123")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(init_and_seed())
