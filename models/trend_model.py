"""
models/trend_model.py
──────────────────────────────────────────────────────────
TrendPredictor — a two-stage model:

  Stage 1  XGBoost Regressor
           → Outputs a continuous trend_score in [0, 1]
           → Trained on labelled synthetic data (see train.py)

  Stage 2  Isolation Forest
           → Flags products with anomalous signal spikes
           → anomaly_detected = True means sudden unexpected growth

  Post-processing:
           → Classify status (viral / hot / rising / stable / cold)
           → Compute 7-day forecast via simple exponential extrapolation
           → Assign confidence based on data volume
"""

import os
import joblib
import numpy as np
import pandas as pd
from datetime import datetime
from typing import List, Tuple, Optional
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import MinMaxScaler
from xgboost import XGBRegressor

from feature_engineering import FEATURE_COLUMNS, extract_features
from schemas import (
    ProductSignals, TrendPrediction, TrendStatus, CategoryTrend
)
from config import settings


# ─────────────────────────────────────────────
#  Status classification
# ─────────────────────────────────────────────

def _classify_status(score: float) -> TrendStatus:
    if score >= settings.VIRAL_THRESHOLD:   return TrendStatus.VIRAL
    if score >= settings.HOT_THRESHOLD:     return TrendStatus.HOT
    if score >= settings.RISING_THRESHOLD:  return TrendStatus.RISING
    if score >= settings.STABLE_THRESHOLD:  return TrendStatus.STABLE
    return TrendStatus.COLD


def _forecast_7d(score: float, acceleration: float) -> float:
    """
    Naive 7-day forecast:
      score_t+7 = score_t + acceleration * growth_factor
    Capped to [0, 1].
    """
    growth_factor = 0.15
    projected = score + acceleration * growth_factor
    return float(np.clip(projected, 0.0, 1.0))


def _confidence(signals: ProductSignals, score: float) -> float:
    """
    Confidence is higher when there is more data volume.
    Low-volume products get lower confidence even if score is high.
    """
    volume = (
        signals.views_last_7d    +
        signals.searches_last_7d +
        signals.wishlist_last_7d +
        signals.cart_last_7d
    )
    vol_conf = float(np.clip(np.log1p(volume) / np.log1p(5000), 0.1, 1.0))
    # blend with score-based confidence (extreme scores = more certain)
    score_conf = abs(score - 0.5) * 2.0  # 0 at 0.5, 1 at extremes
    return float(np.clip(0.6 * vol_conf + 0.4 * score_conf, 0.1, 0.99))


# ─────────────────────────────────────────────
#  Model class
# ─────────────────────────────────────────────

class TrendPredictor:
    def __init__(self):
        self.regressor:       Optional[XGBRegressor]    = None
        self.anomaly_detector:Optional[IsolationForest] = None
        self.scaler:          Optional[MinMaxScaler]    = None
        self.is_fitted:       bool                      = False
        self.training_samples:int                       = 0
        self.last_trained:    str                       = ""
        self.supported_categories: List[str]            = []

    # ── Training ──────────────────────────────

    def fit(self, X: pd.DataFrame, y: np.ndarray):
        """
        X : feature DataFrame with FEATURE_COLUMNS columns
        y : trend_score labels in [0, 1]
        """
        X_feat = X[FEATURE_COLUMNS].fillna(0.0)

        # scale for isolation forest
        self.scaler = MinMaxScaler()
        X_scaled    = self.scaler.fit_transform(X_feat)

        # Stage 1 – regression
        self.regressor = XGBRegressor(
            n_estimators      = 300,
            max_depth         = 6,
            learning_rate     = 0.05,
            subsample         = 0.8,
            colsample_bytree  = 0.8,
            min_child_weight  = 3,
            gamma             = 0.1,
            reg_alpha         = 0.1,
            reg_lambda        = 1.0,
            objective         = "reg:squarederror",
            eval_metric       = "rmse",
            random_state      = 42,
            n_jobs            = -1,
        )
        self.regressor.fit(
            X_feat, y,
            eval_set   = [(X_feat, y)],
            verbose    = False,
        )

        # Stage 2 – anomaly detection
        self.anomaly_detector = IsolationForest(
            n_estimators    = 200,
            contamination   = 0.08,   # ~8% of products are anomalous
            max_features    = 0.7,
            random_state    = 42,
        )
        self.anomaly_detector.fit(X_scaled)

        self.is_fitted         = True
        self.training_samples  = len(X)
        self.last_trained      = datetime.utcnow().isoformat() + "Z"

        if "category" in X.columns:
            self.supported_categories = sorted(X["category"].unique().tolist())

    # ── Inference ─────────────────────────────

    def predict(
        self,
        signals: List[ProductSignals],
        top_n:   int = 10,
        category_filter: Optional[str] = None,
    ) -> Tuple[List[TrendPrediction], List[CategoryTrend]]:

        if not self.is_fitted:
            raise RuntimeError("Model not fitted. Call fit() or load() first.")

        feature_df = extract_features(signals)

        if feature_df.empty:
            return [], []

        X_feat   = feature_df[FEATURE_COLUMNS].fillna(0.0)
        X_scaled = self.scaler.transform(X_feat)

        raw_scores = self.regressor.predict(X_feat).astype(float)
        raw_scores = np.clip(raw_scores, 0.0, 1.0)

        # anomaly: -1 = anomaly, 1 = normal  →  convert to bool
        anomaly_labels = self.anomaly_detector.predict(X_scaled)
        anomalies      = (anomaly_labels == -1)

        predictions = []
        for i, sig in enumerate(signals):
            if category_filter and sig.category.lower() != category_filter.lower():
                continue

            score      = float(raw_scores[i])
            feat_row   = feature_df.iloc[i]
            is_anomaly = bool(anomalies[i])

            # anomaly boosts score slightly — real spike
            if is_anomaly:
                score = float(np.clip(score * 1.15, 0.0, 1.0))

            predictions.append(TrendPrediction(
                product_id       = sig.product_id,
                category         = sig.category,
                trend_score      = round(score, 4),
                trend_status     = _classify_status(score),
                confidence       = round(_confidence(sig, score), 4),
                view_velocity    = round(float(feat_row["view_velocity"]), 4),
                search_momentum  = round(float(feat_row["search_momentum"]), 4),
                wishlist_signal  = round(float(feat_row["wishlist_rate"]), 4),
                cart_intent      = round(float(feat_row["cart_rate"]), 4),
                anomaly_detected = is_anomaly,
                forecast_7d      = round(_forecast_7d(score, float(feat_row["acceleration"])), 4),
                rank             = 0,   # assigned below
            ))

        # rank by trend_score descending
        predictions.sort(key=lambda p: p.trend_score, reverse=True)
        for rank, pred in enumerate(predictions, start=1):
            pred.rank = rank

        top_predictions = predictions[:top_n]

        # ── Category summary ──
        cat_groups: dict = {}
        for p in predictions:
            cat = p.category
            if cat not in cat_groups:
                cat_groups[cat] = {"scores": [], "products": [], "top": p}
            cat_groups[cat]["scores"].append(p.trend_score)
            cat_groups[cat]["products"].append(p.product_id)
            if p.trend_score > cat_groups[cat]["top"].trend_score:
                cat_groups[cat]["top"] = p

        category_trends = []
        for cat, data in cat_groups.items():
            scores    = data["scores"]
            prev_mean = np.mean(scores[len(scores)//2:]) if len(scores) > 1 else scores[0]
            curr_mean = np.mean(scores[:len(scores)//2]) if len(scores) > 1 else scores[0]
            momentum  = float(np.clip((curr_mean - prev_mean) / (prev_mean + 1e-9), -1, 1))

            category_trends.append(CategoryTrend(
                category          = cat,
                avg_trend_score   = round(float(np.mean(scores)), 4),
                trending_products = sum(1 for s in scores if s >= settings.RISING_THRESHOLD),
                top_product_id    = data["top"].product_id,
                momentum          = round(momentum, 4),
            ))

        category_trends.sort(key=lambda c: c.avg_trend_score, reverse=True)

        return top_predictions, category_trends

    # ── Feature importance ────────────────────

    def feature_importance(self) -> dict:
        if not self.is_fitted:
            return {}
        scores = self.regressor.feature_importances_
        return {col: round(float(s), 4) for col, s in zip(FEATURE_COLUMNS, scores)}

    # ── Persistence ───────────────────────────

    def save(self, path: str = settings.MODEL_PATH):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        joblib.dump(self, path)
        print(f"[TrendPredictor] Model saved → {path}")

    @classmethod
    def load(cls, path: str = settings.MODEL_PATH) -> "TrendPredictor":
        if not os.path.exists(path):
            raise FileNotFoundError(f"Model file not found: {path}")
        model = joblib.load(path)
        print(f"[TrendPredictor] Model loaded ← {path}")
        return model
