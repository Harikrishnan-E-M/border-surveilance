"""Database seeder script for VisionAI.

Creates default organization, admin user, and sample data for development.
Idempotent: checks for existing data before inserting.

Usage:
    python scripts/seed_data.py
"""

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone

# Add the backend directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


async def seed_database() -> None:
    """Create default organization, users, and sample configuration."""
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://visionai:visionai_password@localhost:5432/visionai",
    )

    engine = create_async_engine(database_url, echo=False)

    # Import models after path setup
    from app.models.organization import Organization, SubscriptionTier
    from app.models.user import User, UserRole

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        async with session.begin():
            # Check if default org exists
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
                print(f"Created organization: {org.name} (ID: {org.id})")
            else:
                print(f"Organization already exists: {org.name} (ID: {org.id})")

            # Create super admin
            admin_email = os.getenv("ADMIN_EMAIL", "admin@visionai.com")
            admin_password = os.getenv("ADMIN_PASSWORD", "admin123456")

            result = await session.execute(
                select(User).where(User.email == admin_email)
            )
            admin = result.scalar_one_or_none()

            if admin is None:
                admin = User(
                    id=uuid.uuid4(),
                    org_id=org.id,
                    email=admin_email,
                    hashed_password=pwd_context.hash(admin_password),
                    full_name="System Administrator",
                    role=UserRole.SUPER_ADMIN,
                    phone="+919999999999",
                    is_active=True,
                    last_login=datetime.now(timezone.utc).replace(tzinfo=None),
                )
                session.add(admin)
                print(f"Created super admin: {admin_email} / {admin_password}")
            else:
                print(f"Admin user already exists: {admin_email}")

            # Create sample users for each role
            sample_users = [
                {
                    "email": "manager@visionai.com",
                    "full_name": "Site Manager",
                    "role": UserRole.ORG_ADMIN,
                    "phone": "+919888888888",
                },
                {
                    "email": "operator@visionai.com",
                    "full_name": "Security Operator",
                    "role": UserRole.OPERATOR,
                    "phone": "+919777777777",
                },
                {
                    "email": "viewer@visionai.com",
                    "full_name": "Dashboard Viewer",
                    "role": UserRole.VIEWER,
                    "phone": "+919666666666",
                },
            ]

            for user_data in sample_users:
                result = await session.execute(
                    select(User).where(User.email == user_data["email"])
                )
                existing = result.scalar_one_or_none()
                if existing is None:
                    user = User(
                        id=uuid.uuid4(),
                        org_id=org.id,
                        email=user_data["email"],
                        hashed_password=pwd_context.hash("password123"),
                        full_name=user_data["full_name"],
                        role=user_data["role"],
                        phone=user_data["phone"],
                        is_active=True,
                    )
                    session.add(user)
                    print(f"Created user: {user_data['email']} (role: {user_data['role'].value})")
                else:
                    print(f"User already exists: {user_data['email']}")

        print("\nSeed data complete.")
        print("=" * 50)
        print("Default credentials:")
        print(f"  Admin: {admin_email} / {admin_password}")
        print("  Manager: manager@visionai.com / password123")
        print("  Operator: operator@visionai.com / password123")
        print("  Viewer: viewer@visionai.com / password123")
        print("=" * 50)

    await engine.dispose()


def main() -> None:
    """Entry point."""
    print("VisionAI Database Seeder")
    print("=" * 50)
    asyncio.run(seed_database())


if __name__ == "__main__":
    main()
