# This file holds all the settings for the entire project.
# Think of it as the control panel — every other file reads from here.
# Values come from the .env file so we never hardcode secrets in code.

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):

    # --- General app settings ---
    APP_NAME: str = "Autonomous Business Intelligence Platform"
    APP_VERSION: str = "1.0.0"
    # When DEBUG is True we get detailed logs and SQL query printing
    DEBUG: bool = True

    # --- MySQL database ---
    # Replace yourpassword with your actual MySQL password
    MYSQL_URL: str = "sqlite+aiosqlite:///D:/Business Intelligent System/data/abip.db"

    # --- JWT token settings ---
    # This secret key is used to sign login tokens — keep it private
    JWT_SECRET_KEY: str = "change-this-to-something-long-and-random"
    JWT_ALGORITHM: str = "HS256"
    # Token stays valid for 7 days
    JWT_EXPIRE_MINUTES: int = 60 * 24 * 7

    # --- Google OAuth2 login ---
    # Get these from https://console.cloud.google.com
    
    GROQ_API_KEY: str = ""
    LLM_PROVIDER: str = "groq"

    # --- Qdrant vector database ---
    # Qdrant stores document embeddings for the RAG agent
    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    QDRANT_COLLECTION: str = "abip_documents"

    # --- File storage paths ---
    # Where uploaded CSV, Excel and PDF files are saved
    UPLOAD_DIR: str = "D:/Business Intelligent System/data/uploads"
    # Where generated reports are saved
    REPORTS_DIR: str = "D:/Business Intelligent System/data/reports"
    # Maximum upload size is 100MB
    MAX_FILE_SIZE_BYTES: int = 100 * 1024 * 1024

    class Config:
        # Reads all values from the .env file automatically
        env_file = "D:/Business Intelligent System/backend/.env"
        env_file_encoding = "utf-8"
        extra = "ignore"


# lru_cache makes sure Settings is only created once
# every file that imports settings gets the exact same object
@lru_cache()
def get_settings() -> Settings:
    return Settings()


# Import this in every other file like this:
# from app.core.config import settings
settings = get_settings()