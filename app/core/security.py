# This file handles all security related things —
# creating login tokens, verifying them, and hashing passwords.
# No other file should do any of this directly.

from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.config import settings

# This tells passlib to use bcrypt for hashing passwords
# bcrypt is the industry standard — never store plain text passwords

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)
# This reads the token from the Authorization header of every request
bearer_scheme = HTTPBearer()


def hash_password(plain_password: str) -> str:
    # Turns "mypassword123" into a long unreadable hash
    # The hash is what gets saved in the database
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    # Checks if the password the user typed matches the stored hash
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(user_id: int, email: str) -> str:
    # Creates a JWT token the user gets after logging in
    expire = datetime.utcnow() + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)

    payload = {
        "sub": str(user_id),  # who this token belongs to
        "email": email,
        "exp": expire,         # when this token expires
    }

    # Signs the token with our secret key so nobody can fake one
    token = jwt.encode(
        payload,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM
    )
    return token


def decode_access_token(token: str) -> Optional[dict]:
    # Reads and verifies a token sent by the user
    # Returns the payload if valid, None if expired or fake
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM]
        )
        return payload
    except JWTError:
        return None


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)
):
    # This protects any route that requires login
    # Add Depends(get_current_user) to any route to make it login-required
    token = credentials.credentials
    payload = decode_access_token(token)

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )

    return payload  # contains sub (user_id) and email
