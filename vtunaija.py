# vtunaija.py
import os
import requests
import logging
from datetime import datetime
from models import db, User, Transaction, Profit

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

VTUNAIJA_API_KEY = os.environ.get("VTUNAIJA_API_KEY", "")
VTUNAIJA_BASE_URL = os.environ.get("VTUNAIJA_BASE_URL", "https://vtunaija.com.ng/api")
PROFIT_MARGIN_EXAM_PIN = float(os.environ.get("PROFIT_MARGIN_EXAM_PIN", "10"))

def vtunaija_request(endpoint, data=None, timeout=30):
    """Wrapper for VtuNaija API calls with timeout and error handling."""
    headers = {
        "Authorization": f"Token {VTUNAIJA_API_KEY}",
        "Content-Type": "application/json"
    }
    url = f"{VTUNAIJA_BASE_URL}/{endpoint}"
    logger.info(f"VtuNaija Request: POST {url} | Data: {data}")
    try:
        response = requests.post(url, json=data, headers=headers, timeout=timeout)
        result = response.json()
        logger.info(f"VtuNaija Response: {result}")
        return result
    except requests.exceptions.Timeout:
        logger.error("VtuNaija request timed out")
        return {"Status": "failed", "api_response": "Request timed out"}
    except requests.exceptions.ConnectionError:
        logger.error("VtuNaija connection error")
        return {"Status": "failed", "api_response": "Network connection error"}
    except Exception as e:
        logger.error(f"VtuNaija request error: {str(e)}")
        return {"Status": "failed", "api_response": f"Request error: {str(e)}"}

def buy_exam_pin(exam_name, quantity, user_email, selling_price=None):
    """
    Purchase exam PIN with wallet validation, pending transaction,
    atomic updates, and proper error handling.
    """
    # 1. Validate inputs
    if quantity < 1 or quantity > 10:
        return {"status": "error", "message": "Quantity must be between 1 and 10."}
    if selling_price is None:
        return {"status": "error", "message": "Selling price is required."}

    # Exam name mapping (adjust IDs as per VtuNaija docs)
    exam_name_map = {"WAEC": "2", "NECO": "3", "NABTEB": "4", "JAMB": "1"}
    exam_id = exam_name_map.get(exam_name.upper(), str(exam_name))

    # 2. Fetch user and validate wallet
    user = User.query.filter_by(email=user_email).first()
    if not user:
        return {"status": "error", "message": "User not found"}

    selling_price = float(selling_price)
    if user.wallet_balance < selling_price:
        return {"status": "error", "message": f"Insufficient balance. Available: ₦{user.wallet_balance:,.2f}"}

    # 3. Create pending transaction
    reference = f"EXAM_{datetime.utcnow().timestamp()}_{user.id}"
    transaction = Transaction(
        user_id=user.id,
        reference=reference,
        type="exam_pin",
        service_type="exam_pin",
        amount=selling_price,
        profit=0.0,
        status="pending",
        details={"exam_name": exam_name, "quantity": quantity}
    )
    db.session.add(transaction)
    db.session.commit()

    # 4. Call external API
    payload = {"exam_name": exam_id, "quantity": str(quantity)}
    api_result = vtunaija_request("exam/", data=payload)

    # 5. Handle API response
    if api_result.get("Status") == "successful" or api_result.get("status") == "success":
        # Extract cost price from API response
        cost_price = float(api_result.get("plan_amount", 0))
        profit_amount = selling_price - cost_price

        user.wallet_balance -= selling_price
        transaction.status = "success"
        transaction.profit = profit_amount
        transaction.details.update({
            "cost_price": cost_price,
            "pin": api_result.get("pin"),
            "serial": api_result.get("serial"),
            "api_transaction_id": api_result.get("id")
        })

        profit = Profit(
            transaction_id=transaction.id,
            user_id=user.id,
            category="exam_pin",
            amount=profit_amount
        )
        db.session.add(profit)
        db.session.commit()

        return {
            "status": "success",
            "message": f"{exam_name} PIN purchase successful",
            "data": {
                "transaction_id": api_result.get("id"),
                "pin": api_result.get("pin"),
                "serial": api_result.get("serial"),
                "profit_amount": profit_amount,
                "selling_price": selling_price,
                "reference": reference
            }
        }
    else:
        transaction.status = "failed"
        transaction.details["error"] = api_result.get("api_response") or api_result.get("message", "Exam PIN purchase failed")
        db.session.commit()
        return {"status": "error", "message": "Exam PIN purchase failed"}
