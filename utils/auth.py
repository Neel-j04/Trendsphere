"""
utils/auth.py
─────────────────────────────────────────────────
Simple API-key authentication for the trend API.
Replace with JWT or OAuth in production.
"""

from fastapi import HTTPException, Security, status
from fastapi.security.api_key import APIKeyHeader
from config import settings

api_key_scheme = APIKeyHeader(name=settings.API_KEY_HEADER, auto_error=False)


async def verify_api_key(api_key: str = Security(api_key_scheme)) -> str:
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Include 'X-API-Key' header.",
        )
    if api_key not in settings.VALID_API_KEYS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key.",
        )
    return api_key
