"""
train.py
──────────────────────────────────────────────────────────
Generates synthetic labelled training data and trains the
TrendPredictor model. Run this once before starting the API.

Usage:
    python train.py
    python train.py --samples 5000 --output models/saved/trend_model.pkl
"""

import argparse
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score

from schemas import ProductSignals
from feature_engineering import extract_features, FEATURE_COLUMNS
from models.trend_model import TrendPredictor
from config import settings


CATEGORIES = [
    "electronics", "fashion", "home_decor", "beauty", "sports",
    "books", "toys", "kitchen", "fitness", "gaming",
]


# ─────────────────────────────────────────────
#  Synthetic data generation
# ─────────────────────────────────────────────

def generate_synthetic_signals(n_samples: int = 3000, seed: int = 42) -> pd.DataFrame:
    """
    Creates realistic synthetic ProductSignals with known trend patterns.
    Returns a DataFrame that includes a 'true_trend_score' label column.
    """
    rng = np.random.default_rng(seed)

    # Simulate 4 archetypes of products:
    #  1. viral    (20%) – all signals surging
    #  2. rising   (25%) – moderate consistent growth
    #  3. stable   (30%) – flat / slow growth
    #  4. declining(25%) – falling signals
    archetype_probs = [0.20, 0.25, 0.30, 0.25]
    archetypes      = rng.choice(["viral","rising","stable","declining"],
                                  p=archetype_probs, size=n_samples)

    records = []
    for i, arch in enumerate(archetypes):
        pid = f"prod_{i:05d}"
        cat = rng.choice(CATEGORIES)

        if arch == "viral":
            base_views  = int(rng.integers(500,  5000))
            view_mult   = float(rng.uniform(3.0, 10.0))
            search_mult = float(rng.uniform(2.5,  8.0))
            wish_mult   = float(rng.uniform(2.0,  6.0))
            cart_mult   = float(rng.uniform(1.8,  5.0))
            true_score  = float(rng.uniform(0.80, 1.00))

        elif arch == "rising":
            base_views  = int(rng.integers(200,  3000))
            view_mult   = float(rng.uniform(1.5,  3.0))
            search_mult = float(rng.uniform(1.3,  2.5))
            wish_mult   = float(rng.uniform(1.2,  2.0))
            cart_mult   = float(rng.uniform(1.1,  1.8))
            true_score  = float(rng.uniform(0.45, 0.80))

        elif arch == "stable":
            base_views  = int(rng.integers(100,  2000))
            view_mult   = float(rng.uniform(0.85, 1.50))
            search_mult = float(rng.uniform(0.90, 1.40))
            wish_mult   = float(rng.uniform(0.85, 1.30))
            cart_mult   = float(rng.uniform(0.90, 1.20))
            true_score  = float(rng.uniform(0.20, 0.45))

        else:  # declining
            base_views  = int(rng.integers(50,   1500))
            view_mult   = float(rng.uniform(0.20, 0.85))
            search_mult = float(rng.uniform(0.15, 0.80))
            wish_mult   = float(rng.uniform(0.10, 0.75))
            cart_mult   = float(rng.uniform(0.10, 0.70))
            true_score  = float(rng.uniform(0.00, 0.20))

        # add noise
        noise = float(rng.normal(0, 0.03))
        true_score = float(np.clip(true_score + noise, 0.0, 1.0))

        prev_views    = base_views
        recent_views  = int(base_views * view_mult)
        prev_searches = max(0, int(base_views * float(rng.uniform(0.1, 0.4))))
        recent_searches = int(prev_searches * search_mult)
        prev_wish     = max(0, int(prev_views * float(rng.uniform(0.02, 0.10))))
        recent_wish   = int(prev_wish * wish_mult)
        prev_cart     = max(0, int(prev_views * float(rng.uniform(0.01, 0.06))))
        recent_cart   = int(prev_cart * cart_mult)

        records.append({
            "product_id":        pid,
            "category":          cat,
            "views_last_7d":     recent_views,
            "views_prev_7d":     prev_views,
            "searches_last_7d":  max(0, recent_searches),
            "searches_prev_7d":  max(0, prev_searches),
            "wishlist_last_7d":  max(0, recent_wish),
            "wishlist_prev_7d":  max(0, prev_wish),
            "cart_last_7d":      max(0, recent_cart),
            "cart_prev_7d":      max(0, prev_cart),
            "purchases_last_7d": max(0, int(recent_cart * float(rng.uniform(0.3, 0.8)))),
            "avg_rating":        float(rng.uniform(2.5, 5.0)),
            "review_count":      int(rng.integers(0, 2000)),
            "price":             float(rng.uniform(5, 5000)),
            "true_trend_score":  true_score,
            "archetype":         arch,
        })

    return pd.DataFrame(records)


# ─────────────────────────────────────────────
#  Training pipeline
# ─────────────────────────────────────────────

def train(n_samples: int = 3000, output_path: str = settings.MODEL_PATH):
    print(f"\n{'═'*55}")
    print("  Trend Prediction Model — Training Pipeline")
    print(f"{'═'*55}\n")

    # 1. Generate data
    print(f"[1/4] Generating {n_samples} synthetic training samples …")
    df = generate_synthetic_signals(n_samples)
    print(f"      Archetype distribution:\n{df['archetype'].value_counts().to_string()}\n")

    # 2. Feature engineering
    print("[2/4] Extracting features …")
    signal_objs = [
        ProductSignals(**{k: v for k, v in row.items()
                          if k not in ("true_trend_score", "archetype")})
        for _, row in df.iterrows()
    ]
    feature_df = extract_features(signal_objs)
    X = feature_df[FEATURE_COLUMNS].fillna(0.0)
    y = df["true_trend_score"].values
    print(f"      Feature matrix: {X.shape[0]} rows × {X.shape[1]} features\n")

    # 3. Train / test split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.20, random_state=42
    )
    feature_df_train = feature_df.iloc[X_train.index]

    # 4. Fit model
    print("[3/4] Training XGBoost + IsolationForest …")
    model = TrendPredictor()
    model.fit(feature_df_train.assign(**{c: X_train[c] for c in FEATURE_COLUMNS}), y_train)

    # 5. Evaluate
    print("[4/4] Evaluating on held-out test set …")
    test_signals = [signal_objs[i] for i in X_test.index]
    preds, _     = model.predict(test_signals, top_n=len(test_signals))
    y_hat        = np.array([p.trend_score for p in preds])

    # align order: preds are sorted by score, need original order
    pred_map = {p.product_id: p.trend_score for p in preds}
    y_hat_ordered = np.array([
        pred_map.get(signal_objs[i].product_id, 0.0) for i in X_test.index
    ])

    rmse = float(np.sqrt(mean_squared_error(y_test, y_hat_ordered)))
    mae  = float(mean_absolute_error(y_test, y_hat_ordered))
    r2   = float(r2_score(y_test, y_hat_ordered))

    print(f"\n  ┌─────────────────────────────┐")
    print(f"  │  RMSE         : {rmse:.4f}       │")
    print(f"  │  MAE          : {mae:.4f}       │")
    print(f"  │  R²           : {r2:.4f}       │")
    print(f"  └─────────────────────────────┘")

    # store metrics on model object for /model/info endpoint
    model._accuracy_metrics = {"rmse": rmse, "mae": mae, "r2": r2}

    # top features
    importance = model.feature_importance()
    top5 = sorted(importance.items(), key=lambda x: x[1], reverse=True)[:5]
    print(f"\n  Top-5 features by importance:")
    for feat, score in top5:
        print(f"    {feat:<25} {score:.4f}")

    # 6. Save
    model.save(output_path)
    print(f"\n✅  Model saved → {output_path}\n")
    return model


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=3000)
    parser.add_argument("--output",  type=str, default=settings.MODEL_PATH)
    args = parser.parse_args()
    train(n_samples=args.samples, output_path=args.output)
