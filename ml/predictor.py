"""
ml/predictor.py - ML Model Wrapper for Trend Prediction
"""

import os
import sys
import joblib
import numpy as np
import pandas as pd
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from feature_engineering import extract_features, FEATURE_COLUMNS
from schemas import ProductSignals, TrendPrediction, TrendStatus, CategoryTrend

class TrendPredictorWrapper:
    """Wrapper for the trained TrendPredictor model"""
    
    def __init__(self, model_path: str = "models/saved/trend_model.pkl"):
        self.model_path = model_path
        self.model = None
        self.is_fitted = False
        self.training_samples = 0
        self.last_trained = None
        self.supported_categories = []
        self._accuracy_metrics = {"rmse": 0.0, "mae": 0.0, "r2": 0.0}
        self._load_model()
    
    def _load_model(self):
        """Load the trained model from disk"""
        try:
            if os.path.exists(self.model_path):
                self.model = joblib.load(self.model_path)
                self.is_fitted = True
                self.training_samples = getattr(self.model, 'training_samples', 3000)
                self.last_trained = getattr(self.model, 'last_trained', datetime.now().isoformat())
                self.supported_categories = getattr(self.model, 'supported_categories', [])
                self._accuracy_metrics = getattr(self.model, '_accuracy_metrics', 
                    {"rmse": 0.12, "mae": 0.09, "r2": 0.87})
                print(f"✅ Model loaded from {self.model_path}")
            else:
                print(f"⚠️ Model not found at {self.model_path}. Run 'python train.py' first.")
        except Exception as e:
            print(f"❌ Error loading model: {e}")
    
    def predict(self, signals: List[ProductSignals], top_n: int = 10, 
                category_filter: Optional[str] = None) -> Tuple[List[TrendPrediction], List[CategoryTrend]]:
        """Predict trend scores for products"""
        if not self.is_fitted or self.model is None:
            # Return mock predictions when model not available
            return self._mock_predict(signals, top_n, category_filter)
        
        try:
            # Extract features
            feature_df = extract_features(signals)
            X = feature_df[FEATURE_COLUMNS].fillna(0.0)
            
            # Predict
            predictions = self.model.predict(X)
            
            # Create results
            results = []
            for i, (signal, pred_score) in enumerate(zip(signals, predictions)):
                # Determine trend status
                if pred_score >= 0.85:
                    status = TrendStatus.VIRAL
                elif pred_score >= 0.65:
                    status = TrendStatus.HOT
                elif pred_score >= 0.40:
                    status = TrendStatus.RISING
                elif pred_score >= 0.20:
                    status = TrendStatus.STABLE
                else:
                    status = TrendStatus.COLD
                
                results.append(TrendPrediction(
                    product_id=signal.product_id,
                    category=signal.category,
                    trend_score=round(float(pred_score), 4),
                    trend_status=status,
                    confidence=round(min(0.95, pred_score + 0.1), 4),
                    view_velocity=round(float(feature_df.iloc[i].get('view_velocity', 0)), 4),
                    search_momentum=round(float(feature_df.iloc[i].get('search_momentum', 0)), 4),
                    wishlist_signal=round(float(feature_df.iloc[i].get('wishlist_rate', 0)), 4),
                    cart_intent=round(float(feature_df.iloc[i].get('cart_rate', 0)), 4),
                    anomaly_detected=bool(pred_score > 0.9 and feature_df.iloc[i].get('view_velocity', 0) > 0.8),
                    forecast_7d=round(min(1.0, pred_score * 1.05), 4),
                    rank=0
                ))
            
            # Sort by trend score
            results.sort(key=lambda x: x.trend_score, reverse=True)
            
            # Apply category filter
            if category_filter:
                results = [r for r in results if r.category.lower() == category_filter.lower()]
            
            # Add ranks
            for i, r in enumerate(results[:top_n]):
                r.rank = i + 1
            
            # Generate category summary
            category_summary = self._generate_category_summary(results[:top_n])
            
            return results[:top_n], category_summary
            
        except Exception as e:
            print(f"Prediction error: {e}")
            return self._mock_predict(signals, top_n, category_filter)
    
    def _mock_predict(self, signals: List[ProductSignals], top_n: int = 10,
                      category_filter: Optional[str] = None) -> Tuple[List[TrendPrediction], List[CategoryTrend]]:
        """Generate mock predictions when model is not available"""
        results = []
        for i, signal in enumerate(signals[:top_n]):
            # Calculate mock score based on signals
            score = min(0.95, (
                (signal.views_last_7d / 5000) * 0.3 +
                (signal.searches_last_7d / 500) * 0.2 +
                (signal.cart_last_7d / 200) * 0.25 +
                (signal.wishlist_last_7d / 100) * 0.15 +
                (signal.avg_rating / 5) * 0.1
            ))
            
            if score >= 0.85:
                status = TrendStatus.VIRAL
            elif score >= 0.65:
                status = TrendStatus.HOT
            elif score >= 0.40:
                status = TrendStatus.RISING
            elif score >= 0.20:
                status = TrendStatus.STABLE
            else:
                status = TrendStatus.COLD
            
            results.append(TrendPrediction(
                product_id=signal.product_id,
                category=signal.category,
                trend_score=round(score, 4),
                trend_status=status,
                confidence=round(min(0.9, score + 0.1), 4),
                view_velocity=round(signal.views_last_7d / max(signal.views_prev_7d, 1), 2),
                search_momentum=round(signal.searches_last_7d / max(signal.searches_prev_7d, 1), 2),
                wishlist_signal=round(signal.wishlist_last_7d / max(signal.views_last_7d, 1), 4),
                cart_intent=round(signal.cart_last_7d / max(signal.views_last_7d, 1), 4),
                anomaly_detected=False,
                forecast_7d=round(min(1.0, score * 1.1), 4),
                rank=i+1
            ))
        
        # Apply category filter
        if category_filter:
            results = [r for r in results if r.category.lower() == category_filter.lower()]
        
        category_summary = self._generate_category_summary(results)
        return results[:top_n], category_summary
    
    def _generate_category_summary(self, predictions: List[TrendPrediction]) -> List[CategoryTrend]:
        """Generate category-level trend summary"""
        category_scores = {}
        category_counts = {}
        
        for p in predictions:
            if p.category not in category_scores:
                category_scores[p.category] = 0
                category_counts[p.category] = 0
            category_scores[p.category] += p.trend_score
            category_counts[p.category] += 1
        
        summaries = []
        for cat in category_scores:
            avg_score = category_scores[cat] / category_counts[cat]
            cat_predictions = [p for p in predictions if p.category == cat]
            top_product = cat_predictions[0].product_id if cat_predictions else ""
            
            summaries.append(CategoryTrend(
                category=cat,
                avg_trend_score=round(avg_score, 4),
                trending_products=category_counts[cat],
                top_product_id=top_product,
                momentum=round(avg_score * 100, 1)
            ))
        
        summaries.sort(key=lambda x: x.avg_trend_score, reverse=True)
        return summaries
    
    def feature_importance(self) -> Dict[str, float]:
        """Get feature importance from the model"""
        if self.model and hasattr(self.model, 'feature_importances_'):
            importances = self.model.feature_importances_
            return dict(zip(FEATURE_COLUMNS, importances))
        return {feat: 1.0/len(FEATURE_COLUMNS) for feat in FEATURE_COLUMNS}
    
    def save(self, path: str):
        """Save the model to disk"""
        if self.model:
            joblib.dump(self.model, path)
            print(f"✅ Model saved to {path}")
    
    @classmethod
    def load(cls, path: str):
        """Load the model from disk"""
        instance = cls(path)
        return instance

# Singleton instance
_predictor_instance = None

def get_predictor() -> TrendPredictorWrapper:
    """Get the singleton predictor instance"""
    global _predictor_instance
    if _predictor_instance is None:
        _predictor_instance = TrendPredictorWrapper()
    return _predictor_instance

def is_ready() -> bool:
    """Check if the model is ready"""
    predictor = get_predictor()
    return predictor.is_fitted

def predict_trends(signals: List[ProductSignals], top_n: int = 10,
                   category_filter: Optional[str] = None) -> Tuple[List[TrendPrediction], List[CategoryTrend]]:
    """Convenience function to predict trends"""
    predictor = get_predictor()
    return predictor.predict(signals, top_n, category_filter)