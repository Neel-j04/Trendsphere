"""
feature_engineering.py
─────────────────────────────────────────────────────────
Converts raw behavioral events or pre-aggregated signals
into the feature vector consumed by the ML model.

Features:
  view_velocity       – % growth in views (recent vs prev period)
  search_momentum     – % growth in searches
  wishlist_rate       – wishlist adds / views (intent signal)
  cart_rate           – cart adds / views (purchase intent)
  purchase_rate       – purchases / views (conversion proxy)
  engagement_score    – weighted combo of all actions
  trend_acceleration  – second-order: is growth speeding up?
  social_proof        – rating × log(review_count+1)
  price_sensitivity   – inverse normalised price (if available)
  category_heat       – relative score within category
"""

import numpy as np
import pandas as pd
from typing import List, Optional, Dict
from collections import defaultdict

from schemas import BehaviorEvent, ProductSignals


# ─────────────────────────────────────────────
#  Aggregate raw events → ProductSignals
# ─────────────────────────────────────────────

def aggregate_events(events: List[BehaviorEvent]) -> List[ProductSignals]:
    """
    Given a list of raw BehaviorEvent objects, split them into
    two equal-time buckets (recent half vs previous half) and
    return aggregated ProductSignals per product.
    """
    from datetime import datetime, timedelta

    records = [e.model_dump() for e in events]
    df = pd.DataFrame(records)

    # parse timestamps; fall back to sequential index if missing
    if df["timestamp"].notna().any():
        df["ts"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df["ts"].fillna(pd.Timestamp.now(), inplace=True)
    else:
        df["ts"] = pd.date_range(end=pd.Timestamp.now(), periods=len(df), freq="min")

    mid = df["ts"].min() + (df["ts"].max() - df["ts"].min()) / 2

    recent = df[df["ts"] >= mid]
    prev   = df[df["ts"] <  mid]

    def count_events(subset, etype):
        return subset[subset["event_type"] == etype].groupby("product_id").size()

    signals_list = []
    for pid in df["product_id"].unique():
        cat = df[df["product_id"] == pid]["category"].iloc[0]

        def _get(subset, etype):
            s = count_events(subset, etype)
            return int(s.get(pid, 0))

        signals_list.append(ProductSignals(
            product_id       = pid,
            category         = cat,
            views_last_7d    = _get(recent, "view"),
            views_prev_7d    = _get(prev,   "view"),
            searches_last_7d = _get(recent, "search"),
            searches_prev_7d = _get(prev,   "search"),
            wishlist_last_7d = _get(recent, "wishlist"),
            wishlist_prev_7d = _get(prev,   "wishlist"),
            cart_last_7d     = _get(recent, "cart"),
            cart_prev_7d     = _get(prev,   "cart"),
            purchases_last_7d= _get(recent, "purchase"),
        ))
    return signals_list


# ─────────────────────────────────────────────
#  Core feature extraction
# ─────────────────────────────────────────────

def _safe_velocity(recent: int, prev: int) -> float:
    """Percentage change capped at ±500 % and normalised to [-1, 1]."""
    if prev == 0:
        return 1.0 if recent > 0 else 0.0
    raw = (recent - prev) / (prev + 1e-9)
    capped = np.clip(raw, -5.0, 5.0)
    return float(capped / 5.0)   # normalise to [-1, 1]


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return float(np.clip(numerator / (denominator + 1e-9), 0.0, 1.0))


def extract_features(signals: List[ProductSignals]) -> pd.DataFrame:
    """
    Returns a DataFrame of shape (n_products, n_features).
    Row order matches the input list.
    """
    rows = []
    for s in signals:
        total_views   = s.views_last_7d + s.views_prev_7d + 1
        total_searches= s.searches_last_7d + s.searches_prev_7d + 1

        view_velocity    = _safe_velocity(s.views_last_7d,    s.views_prev_7d)
        search_momentum  = _safe_velocity(s.searches_last_7d, s.searches_prev_7d)
        wishlist_velocity= _safe_velocity(s.wishlist_last_7d, s.wishlist_prev_7d)
        cart_velocity    = _safe_velocity(s.cart_last_7d,     s.cart_prev_7d)

        wishlist_rate    = _safe_rate(s.wishlist_last_7d, s.views_last_7d)
        cart_rate        = _safe_rate(s.cart_last_7d,     s.views_last_7d)
        purchase_rate    = _safe_rate(s.purchases_last_7d,s.views_last_7d)
        search_to_view   = _safe_rate(s.searches_last_7d, s.views_last_7d)

        # engagement = weighted sum of intent signals
        engagement_score = (
            0.20 * view_velocity   +
            0.25 * search_momentum +
            0.30 * wishlist_rate   +
            0.25 * cart_rate
        )

        # second order: acceleration (are all signals rising together?)
        velocities = [view_velocity, search_momentum, wishlist_velocity, cart_velocity]
        positive   = sum(1 for v in velocities if v > 0)
        acceleration = positive / 4.0   # 0 = all falling, 1 = all rising

        social_proof = (
            s.avg_rating * np.log1p(s.review_count)
        ) / (5.0 * np.log1p(10000))     # normalise assuming max ~10k reviews

        # price sensitivity: cheaper products trend faster in most categories
        price_norm = 0.5  # neutral default when price unknown
        if s.price and s.price > 0:
            price_norm = float(np.clip(1.0 - np.log1p(s.price) / np.log1p(100000), 0, 1))

        rows.append({
            "product_id":        s.product_id,
            "category":          s.category,
            "view_velocity":     view_velocity,
            "search_momentum":   search_momentum,
            "wishlist_velocity": wishlist_velocity,
            "cart_velocity":     cart_velocity,
            "wishlist_rate":     wishlist_rate,
            "cart_rate":         cart_rate,
            "purchase_rate":     purchase_rate,
            "search_to_view":    search_to_view,
            "engagement_score":  engagement_score,
            "acceleration":      acceleration,
            "social_proof":      social_proof,
            "price_sensitivity": price_norm,
            "raw_views_recent":  np.log1p(s.views_last_7d),
            "raw_searches_recent": np.log1p(s.searches_last_7d),
        })

    df = pd.DataFrame(rows)

    # category_heat: how hot is each product relative to its category peers
    if not df.empty:
        cat_mean = df.groupby("category")["engagement_score"].transform("mean")
        cat_std  = df.groupby("category")["engagement_score"].transform("std").replace(0, 1)
        df["category_heat"] = ((df["engagement_score"] - cat_mean) / cat_std).clip(-3, 3) / 3.0

    return df


FEATURE_COLUMNS = [
    "view_velocity", "search_momentum", "wishlist_velocity", "cart_velocity",
    "wishlist_rate", "cart_rate", "purchase_rate", "search_to_view",
    "engagement_score", "acceleration", "social_proof", "price_sensitivity",
    "raw_views_recent", "raw_searches_recent", "category_heat",
]
