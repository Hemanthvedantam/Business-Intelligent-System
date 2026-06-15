# This file defines the users table in MySQL.
# Every person who logs into the platform has a row in this table.

from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.sql import func
from app.db.session import Base


class User(Base):
    # This is the actual table name in MySQL
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    email = Column(String(150), unique=True, nullable=False, index=True)

    # Password is stored as a bcrypt hash — never plain text
    # nullable=True because Google OAuth users don't have a password
    hashed_password = Column(String(255), nullable=True)

    # If the user signed in with Google this stores their Google account ID
    google_id = Column(String(100), nullable=True, unique=True)

    # Is this user allowed to use the platform
    is_active = Column(Boolean, default=True)

    # Is this user an admin — admins can see all investigations
    is_admin = Column(Boolean, default=False)

    # These are set automatically by MySQL
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<User id={self.id} email={self.email}>"