"""
api.py
──────────────────────────────────────────────────────────
FastAPI application exposing the TrendPredictor as a REST API.

Endpoints:
  GET  /                    → welcome
  GET  /health              → health check
  GET  /model/info          → model metadata & feature importance
  POST /predict/trends      → predict trends from events or signals
  GET  /trends/top          → get current top-N trends (from cache)
  POST /simulate            → simulate signal boost impact
  POST /admin/retrain       → trigger model retraining (admin only)

Run:
  uvicorn api:app --host 0.0.0.0 --port 8000 --reload
"""

import os
import sys
import uuid
import time
import asyncio
from datetime import datetime
from typing import Optional, List

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, Depends, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from config import settings
from schemas import (
    PredictRequest, PredictResponse, SimulateRequest, SimulateResponse,
    TopTrendsResponse, HealthResponse, ModelInfoResponse,
    TrendPrediction, CategoryTrend, ProductSignals,
)
from feature_engineering import aggregate_events, extract_features, FEATURE_COLUMNS
from models.trend_model import TrendPredictor
from utils.auth import verify_api_key


# ─────────────────────────────────────────────
#  App setup
# ─────────────────────────────────────────────

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title       = settings.APP_NAME,
    version     = settings.APP_VERSION,
    description = """
## AI-Based Emerging Product Trend Prediction API

Predict **which products will trend** before they go viral — powered by behavioral signals:
product views, search activity, wishlist additions, and cart interactions.

### Authentication
All endpoints require an `X-API-Key` header.  
Demo key: `demo-key-123`

### Quick start
```bash
curl -X POST http://localhost:8000/predict/trends \\
  -H "X-API-Key: demo-key-123" \\
  -H "Content-Type: application/json" \\
  -d '{"signals": [{"product_id":"p1","category":"electronics",
       "views_last_7d":1200,"views_prev_7d":300,
       "searches_last_7d":400,"searches_prev_7d":80,
       "wishlist_last_7d":90,"wishlist_prev_7d":15,
       "cart_last_7d":60,"cart_prev_7d":10}], "top_n": 5}'
```
""",
    docs_url    = "/docs",
    redoc_url   = "/redoc",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins  = ["*"],
    allow_methods  = ["*"],
    allow_headers  = ["*"],
)

# ─────────────────────────────────────────────
#  Model loading
# ─────────────────────────────────────────────

_model: Optional[TrendPredictor] = None
_start_time = time.time()
_top_trends_cache: dict = {}   # simple in-memory cache


def get_model() -> TrendPredictor:
    global _model
    if _model is None or not _model.is_fitted:
        try:
            _model = TrendPredictor.load(settings.MODEL_PATH)
        except FileNotFoundError:
            raise HTTPException(
                status_code = status.HTTP_503_SERVICE_UNAVAILABLE,
                detail      = "Model not loaded. Run `python train.py` first.",
            )
    return _model


# ─────────────────────────────────────────────
#  Routes
# ─────────────────────────────────────────────

@app.get("/", tags=["General"])
async def root():
    return {
        "name":    settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs":    "/docs",
        "health":  "/health",
    }


@app.get("/health", response_model=HealthResponse, tags=["General"])
async def health():
    global _model
    loaded = False
    try:
        m = get_model()
        loaded = m.is_fitted
    except Exception:
        pass

    return HealthResponse(
        status        = "healthy" if loaded else "degraded",
        model_loaded  = loaded,
        model_version = settings.MODEL_VERSION,
        uptime_seconds= round(time.time() - _start_time, 1),
    )


@app.get("/model/info", response_model=ModelInfoResponse, tags=["Model"])
async def model_info(api_key: str = Depends(verify_api_key)):
    model = get_model()
    metrics = getattr(model, "_accuracy_metrics", {"rmse": 0.0, "mae": 0.0, "r2": 0.0})
    return ModelInfoResponse(
        model_version         = settings.MODEL_VERSION,
        algorithm             = "XGBoost Regressor + IsolationForest Anomaly Detector",
        features_used         = FEATURE_COLUMNS,
        training_samples      = model.training_samples,
        accuracy_metrics      = metrics,
        last_trained          = model.last_trained,
        supported_categories  = model.supported_categories,
    )


@app.post("/predict/trends", response_model=PredictResponse, tags=["Prediction"])
@limiter.limit(settings.RATE_LIMIT)
async def predict_trends(
    request,
    body:    PredictRequest,
    api_key: str = Depends(verify_api_key),
):
    """
    **Main prediction endpoint.**

    Send either:
    - `events` — raw behavioral events (views, searches, wishlist, cart, purchase)
    - `signals` — pre-aggregated per-product metrics for two time windows

    Returns ranked trend predictions with scores, status labels, anomaly flags,
    and a 7-day forecast per product, plus a category-level summary.
    """
    t0    = time.time()
    model = get_model()

    # Resolve signals
    if body.signals:
        signals = body.signals
    else:
        signals = aggregate_events(body.events)

    if not signals:
        raise HTTPException(status_code=400, detail="No processable signals found.")

    try:
        predictions, category_summary = model.predict(
            signals,
            top_n           = body.top_n,
            category_filter = body.category_filter,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prediction error: {str(e)}")

    elapsed = round((time.time() - t0) * 1000, 2)

    # cache for /trends/top
    _top_trends_cache["predictions"]     = predictions
    _top_trends_cache["category_filter"] = body.category_filter
    _top_trends_cache["updated_at"]      = datetime.utcnow().isoformat() + "Z"

    return PredictResponse(
        request_id        = str(uuid.uuid4()),
        timestamp         = datetime.utcnow().isoformat() + "Z",
        model_version     = settings.MODEL_VERSION,
        products          = predictions,
        category_summary  = category_summary,
        total_analyzed    = len(signals),
        processing_time_ms= elapsed,
    )


@app.get("/trends/top", response_model=TopTrendsResponse, tags=["Prediction"])
async def top_trends(
    category: Optional[str] = Query(None, description="Filter by category"),
    limit:    int            = Query(10, ge=1, le=50),
    api_key:  str            = Depends(verify_api_key),
):
    """
    Returns the most recent cached top-N trends.
    Call `/predict/trends` first to populate the cache.
    """
    if not _top_trends_cache.get("predictions"):
        raise HTTPException(
            status_code = 404,
            detail      = "No cached trends. Call POST /predict/trends first.",
        )

    cached: List[TrendPrediction] = _top_trends_cache["predictions"]

    if category:
        cached = [p for p in cached if p.category.lower() == category.lower()]

    return TopTrendsResponse(
        timestamp = _top_trends_cache.get("updated_at", ""),
        category  = category,
        trends    = cached[:limit],
        total     = len(cached[:limit]),
    )


@app.post("/simulate", response_model=SimulateResponse, tags=["Simulation"])
async def simulate(
    body:    SimulateRequest,
    api_key: str = Depends(verify_api_key),
):
    """
    **What-if simulator.**

    Boost a product's behavioral signals by the specified amounts and see
    how the trend score and status would change. Useful for planning
    marketing campaigns or inventory decisions.
    """
    model = get_model()

    # baseline signals
    if body.base_signals:
        base = body.base_signals
    else:
        # cold-start: zero baseline
        base = ProductSignals(
            product_id       = body.product_id,
            category         = body.category,
            views_last_7d    = 100,
            views_prev_7d    = 100,
            searches_last_7d = 20,
            searches_prev_7d = 20,
            wishlist_last_7d = 5,
            wishlist_prev_7d = 5,
            cart_last_7d     = 3,
            cart_prev_7d     = 3,
        )

    # boosted signals
    boosted = ProductSignals(
        product_id       = body.product_id,
        category         = body.category,
        views_last_7d    = base.views_last_7d    + body.boost_views,
        views_prev_7d    = base.views_prev_7d,
        searches_last_7d = base.searches_last_7d + body.boost_searches,
        searches_prev_7d = base.searches_prev_7d,
        wishlist_last_7d = base.wishlist_last_7d + body.boost_wishlist,
        wishlist_prev_7d = base.wishlist_prev_7d,
        cart_last_7d     = base.cart_last_7d     + body.boost_cart,
        cart_prev_7d     = base.cart_prev_7d,
        purchases_last_7d= base.purchases_last_7d,
        avg_rating       = base.avg_rating,
        review_count     = base.review_count,
        price            = base.price,
    )

    base_preds,    _ = model.predict([base],    top_n=1)
    boosted_preds, _ = model.predict([boosted], top_n=1)

    if not base_preds or not boosted_preds:
        raise HTTPException(status_code=500, detail="Simulation failed.")

    curr_score  = base_preds[0].trend_score
    sim_score   = boosted_preds[0].trend_score
    delta       = round(sim_score - curr_score, 4)
    curr_status = base_preds[0].trend_status
    proj_status = boosted_preds[0].trend_status

    # recommendation
    if delta > 0.30:
        rec = "🚀 High impact — this boost would likely make the product go viral."
    elif delta > 0.15:
        rec = "📈 Moderate impact — expect a significant rise in trend rank."
    elif delta > 0.05:
        rec = "📊 Low impact — minor improvement. Consider larger campaigns."
    else:
        rec = "🔍 Minimal impact — organic growth signals are the bigger driver here."

    return SimulateResponse(
        product_id       = body.product_id,
        current_score    = curr_score,
        simulated_score  = sim_score,
        score_delta      = delta,
        current_status   = curr_status,
        projected_status = proj_status,
        recommendation   = rec,
    )


@app.post("/admin/retrain", tags=["Admin"])
async def retrain(
    n_samples: int = Query(3000, ge=500, le=50000),
    api_key:   str = Depends(verify_api_key),
):
    """
    Trigger a model retrain with fresh synthetic data.
    In production, replace the generator with your real event store.
    """
    global _model

    async def _retrain_task():
        global _model
        import importlib
        train_module = importlib.import_module("train")
        _model = train_module.train(n_samples=n_samples)

    asyncio.create_task(_retrain_task())

    return {
        "status":  "retraining_started",
        "samples": n_samples,
        "message": "Retraining running in background. Check /health to confirm.",
    }
