# This file creates and manages the MySQL database connection.
# Every part of the app that needs to talk to the database
# gets a connection from here — nothing creates its own connection directly.

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# The engine is the actual connection to MySQL
# pool_size = keep 10 connections open at once
# pool_recycle = restart connections every hour to avoid MySQL timeouts
engine = create_async_engine(
    settings.MYSQL_URL,
    echo=settings.DEBUG,
)

# SessionLocal creates a new database session for each request
SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# Base is the parent class all our database models inherit from
# Every model file does: class User(Base): ...
class Base(DeclarativeBase):
    pass


# This is a FastAPI dependency — routes call this to get a database session
# It automatically closes the session when the request finishes
# Usage in a router:
#   async def my_route(db: AsyncSession = Depends(get_db)):
async def get_db():
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            # Something went wrong — undo any changes to keep data clean
            await session.rollback()
            logger.error("database error", error=str(e))
            raise