import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    # App
    APP_NAME: str = "Trend Prediction API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

    # Security
    SECRET_KEY: str = os.getenv("SECRET_KEY", "your-super-secret-key-change-in-production")
    API_KEY_HEADER: str = "X-API-Key"
    VALID_API_KEYS: list = os.getenv("VALID_API_KEYS", "demo-key-123,test-key-456").split(",")

    # Model
    MODEL_PATH: str = os.getenv("MODEL_PATH", "models/saved/trend_model.pkl")
    MODEL_VERSION: str = "1.0.0"
    RETRAIN_THRESHOLD: int = 1000  # retrain after N new events

    # Rate Limiting
    RATE_LIMIT: str = "100/minute"

    # Trend Score Weights
    VIEW_WEIGHT: float = 0.20
    SEARCH_WEIGHT: float = 0.25
    WISHLIST_WEIGHT: float = 0.30
    CART_WEIGHT: float = 0.25

    # Trend Classification Thresholds
    VIRAL_THRESHOLD: float = 0.85
    HOT_THRESHOLD: float = 0.65
    RISING_THRESHOLD: float = 0.40
    STABLE_THRESHOLD: float = 0.20

settings = Settings()
