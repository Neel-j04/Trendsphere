"""
trend_api.py  —  FastAPI ML service
Runs separately from Flask on port 8001.

Start:
    uvicorn trend_api:app --host 0.0.0.0 --port 8001
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

# Re-export the FastAPI app from the API_Model
from api import app  # noqa: F401  (api.py is the FastAPI app)
