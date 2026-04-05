import os
import requests
from datetime import datetime
from models import db, User, Transaction, Profit

# Configuration
VTUNAIJA_API_KEY = os.environ.get("VTUNAIJA_API_KEY", "")
VTUNAIJA_BASE_URL = os.environ.get("VTUNAIJA_BASE_URL", "https://vtunaija.com.ng/api")
PROFIT_MARGIN_EXAM_PIN = float(os.environ.get("PROFIT_MARGIN_EXAM_PIN", "10"))

def vtunaija_request(endpoint, data=None):
    """Make request to VtuNaija API."""
    headers = {
        "Authorization": f"Token {VTUNAIJA_API_KEY}",
        "Content-Type": "application/json"
    }
    url = f"{VTUNAIJA_BASE_URL}/{endpoint}"
    try:
        response = requests.post(url, json=data, headers=headers, timeout=30)
        return response.json()
    except Exception as e:
        return {"Status": "failed", "api_response": str(e)}

