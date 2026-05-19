from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict
from enum import Enum
from datetime import datetime


# ─────────────────────────────────────────────
#  Enums
# ─────────────────────────────────────────────

class TrendStatus(str, Enum):
    VIRAL   = "viral"
    HOT     = "hot"
    RISING  = "rising"
    STABLE  = "stable"
    COLD    = "cold"

class EventType(str, Enum):
    VIEW      = "view"
    SEARCH    = "search"
    WISHLIST  = "wishlist"
    CART      = "cart"
    PURCHASE  = "purchase"


# ─────────────────────────────────────────────
#  Input Schemas
# ─────────────────────────────────────────────

class BehaviorEvent(BaseModel):
    product_id: str          = Field(..., example="prod_001")
    category:   str          = Field(..., example="electronics")
    event_type: EventType
    timestamp:  Optional[str]= Field(None, example="2024-06-01T10:00:00")
    session_id: Optional[str]= None
    user_segment: Optional[str] = Field(None, example="new_user")

    @field_validator("product_id")
    @classmethod
    def product_id_not_empty(cls, v):
        if not v.strip():
            raise ValueError("product_id cannot be empty")
        return v


class ProductSignals(BaseModel):
    """Pre-aggregated signals for a single product — use this when
    you already have aggregated metrics instead of raw events."""
    product_id:       str   = Field(..., example="prod_001")
    category:         str   = Field(..., example="electronics")
    views_last_7d:    int   = Field(..., ge=0, example=1200)
    views_prev_7d:    int   = Field(..., ge=0, example=400)
    searches_last_7d: int   = Field(..., ge=0, example=340)
    searches_prev_7d: int   = Field(..., ge=0, example=80)
    wishlist_last_7d: int   = Field(..., ge=0, example=90)
    wishlist_prev_7d: int   = Field(..., ge=0, example=20)
    cart_last_7d:     int   = Field(..., ge=0, example=60)
    cart_prev_7d:     int   = Field(..., ge=0, example=15)
    purchases_last_7d:int   = Field(0,  ge=0, example=30)
    avg_rating:       float = Field(0.0,ge=0.0, le=5.0, example=4.2)
    review_count:     int   = Field(0,  ge=0, example=150)
    price:            Optional[float] = Field(None, ge=0, example=1299.99)


class PredictRequest(BaseModel):
    """Send either raw events OR pre-aggregated signals."""
    events:   Optional[List[BehaviorEvent]]  = None
    signals:  Optional[List[ProductSignals]] = None
    top_n:    int = Field(10, ge=1, le=100, description="Number of top trends to return")
    category_filter: Optional[str] = None

    @field_validator("signals", "events")
    @classmethod
    def at_least_one(cls, v):
        return v

    def model_post_init(self, __context):
        if not self.events and not self.signals:
            raise ValueError("Provide either 'events' or 'signals'")


class SimulateRequest(BaseModel):
    """Simulate what happens if you boost a product's signals."""
    product_id:   str
    category:     str
    boost_views:    int   = Field(0, ge=0)
    boost_searches: int   = Field(0, ge=0)
    boost_wishlist: int   = Field(0, ge=0)
    boost_cart:     int   = Field(0, ge=0)
    base_signals: Optional[ProductSignals] = None


# ─────────────────────────────────────────────
#  Output Schemas
# ─────────────────────────────────────────────

class TrendPrediction(BaseModel):
    product_id:       str
    category:         str
    trend_score:      float = Field(..., description="0.0 – 1.0 composite trend score")
    trend_status:     TrendStatus
    confidence:       float = Field(..., description="Model confidence 0.0 – 1.0")
    view_velocity:    float = Field(..., description="% change in views")
    search_momentum:  float = Field(..., description="% change in searches")
    wishlist_signal:  float
    cart_intent:      float
    anomaly_detected: bool  = Field(..., description="True if sudden unexpected spike")
    forecast_7d:      float = Field(..., description="Predicted trend score in 7 days")
    rank:             int


class CategoryTrend(BaseModel):
    category:          str
    avg_trend_score:   float
    trending_products: int
    top_product_id:    str
    momentum:          float


class PredictResponse(BaseModel):
    request_id:        str
    timestamp:         str
    model_version:     str
    products:          List[TrendPrediction]
    category_summary:  List[CategoryTrend]
    total_analyzed:    int
    processing_time_ms:float


class SimulateResponse(BaseModel):
    product_id:         str
    current_score:      float
    simulated_score:    float
    score_delta:        float
    current_status:     TrendStatus
    projected_status:   TrendStatus
    recommendation:     str


class TopTrendsResponse(BaseModel):
    timestamp:   str
    category:    Optional[str]
    trends:      List[TrendPrediction]
    total:       int


class HealthResponse(BaseModel):
    status:        str
    model_loaded:  bool
    model_version: str
    uptime_seconds:float


class ModelInfoResponse(BaseModel):
    model_version:    str
    algorithm:        str
    features_used:    List[str]
    training_samples: int
    accuracy_metrics: Dict[str, float]
    last_trained:     str
    supported_categories: List[str]
