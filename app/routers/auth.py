# This file handles everything related to login and registration.
# Register with email/password or login with Google OAuth2.

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, EmailStr
from app.db.session import get_db
from app.models.user import User
from app.core.security import hash_password, verify_password, create_access_token
from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


# --- Request body models ---
# These define what data the frontend must send

class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# --- HTML page routes ---

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    # Serves the login HTML page
    return templates.TemplateResponse(request=request, name="login.html")

# --- API routes ---

@router.post("/register")
async def register(data: RegisterRequest, db: AsyncSession = Depends(get_db)):
    # Check if someone already registered with this email
    result = await db.execute(select(User).where(User.email == data.email))
    existing = result.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )

    # Create a new user with a hashed password
    new_user = User(
        name=data.name,
        email=data.email,
        hashed_password=hash_password(data.password),
    )
    db.add(new_user)
    await db.flush()  # gets the new user's id without fully committing

    # Create a login token for the new user
    token = create_access_token(user_id=new_user.id, email=new_user.email)
    logger.info("new user registered", email=data.email)

    return {"token": token, "user": {"id": new_user.id, "name": new_user.name, "email": new_user.email}}


@router.post("/login")
async def login(data: LoginRequest, db: AsyncSession = Depends(get_db)):
    # Find the user by email
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()

    # If user not found or password is wrong return the same error
    # We don't say "wrong password" specifically for security reasons
    if not user or not verify_password(data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled"
        )

    token = create_access_token(user_id=user.id, email=user.email)
    logger.info("user logged in", email=data.email)

    return {"token": token, "user": {"id": user.id, "name": user.name, "email": user.email}}