# vtunaija.py — VtuNaija provider integration
# Changes from original:
#   1. Reference IDs now use UUID instead of timestamp (no collisions)
#   2. Added with_for_update() on user query to prevent race conditions
#   3. Added "success" to success status check (was only "successful")
#   4. Non-JSON response handled gracefully

import os
import uuid
import requests
import logging
from datetime import datetime
from models import db, User, Transaction, Profit
from challenge import record_purchase

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

VTUNAIJA_API_KEY      = os.environ.get("VTUNAIJA_API_KEY", "")
VTUNAIJA_BASE_URL     = os.environ.get("VTUNAIJA_BASE_URL", "https://vtunaija.com.ng/api")
PROFIT_MARGIN_EXAM_PIN = float(os.environ.get("PROFIT_MARGIN_EXAM_PIN", "10"))

# Exam name → VtuNaija API ID mapping
EXAM_ID_MAP = {"WAEC": "2", "NECO": "3", "NABTEB": "4", "JAMB": "1"}


def vtunaija_request(endpoint, data=None, timeout=30):
    """Wrapper for VtuNaija API calls with timeout and error handling."""
    headers = {
        "Authorization": f"Token {VTUNAIJA_API_KEY}",
        "Content-Type":  "application/json",
    }
    url = f"{VTUNAIJA_BASE_URL.rstrip('/')}/{endpoint.lstrip('/')}"
    logger.info(f"[VtuNaija] POST {url} | data={data}")
    try:
        response = requests.post(url, json=data, headers=headers, timeout=timeout)
        try:
            result = response.json()
        except Exception:
            logger.error(f"[VtuNaija] Non-JSON response HTTP {response.status_code}: {response.text[:200]}")
            return {"Status": "failed", "api_response": f"Provider error (HTTP {response.status_code})"}
        logger.info(f"[VtuNaija] Response: {result}")
        return result
    except requests.exceptions.Timeout:
        logger.error("[VtuNaija] Request timed out")
        return {"Status": "failed", "api_response": "Request timed out"}
    except requests.exceptions.ConnectionError:
        logger.error("[VtuNaija] Connection error")
        return {"Status": "failed", "api_response": "Network connection error"}
    except Exception as e:
        logger.error(f"[VtuNaija] Exception: {e}")
        return {"Status": "failed", "api_response": f"Request error: {str(e)}"}


def _make_reference(prefix, user_id):
    """Generate a unique reference using UUID — no timestamp collisions."""
    short = uuid.uuid4().hex[:8].upper()
    return f"{prefix}_{short}_{user_id}"


def _is_success(api_result):
    """Check if VtuNaija returned a success status."""
    status = str(api_result.get("Status") or api_result.get("status") or "").lower()
    return status in ("successful", "success", "true")


def buy_exam_pin(exam_name, quantity, user_email, selling_price=None):
    """
    Purchase exam PIN via VtuNaija.
    exam_name: WAEC | NECO | NABTEB | JAMB
    quantity:  1–10
    selling_price: total amount charged to user's wallet
    """
    # 1. Validate inputs
    exam_name = str(exam_name).upper().strip()
    if exam_name not in EXAM_ID_MAP:
        return {"status": "error", "message": f"Unsupported exam type: {exam_name}. Use WAEC, NECO, NABTEB, or JAMB"}

    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        return {"status": "error", "message": "Invalid quantity"}

    if quantity < 1 or quantity > 10:
        return {"status": "error", "message": "Quantity must be between 1 and 10."}

    if selling_price is None:
        return {"status": "error", "message": "Selling price is required."}

    try:
        selling_price = round(float(selling_price), 2)
    except (TypeError, ValueError):
        return {"status": "error", "message": "Invalid selling price"}

    if selling_price <= 0:
        return {"status": "error", "message": "Selling price must be greater than zero"}

    # 2. Fetch user (with row lock to prevent race conditions)
    user = User.query.filter_by(email=user_email).with_for_update().first()
    if not user:
        return {"status": "error", "message": "User not found"}

    # 3. Check wallet
    if user.wallet_balance < selling_price:
        return {"status": "error", "message": f"Insufficient balance. Available: ₦{user.wallet_balance:,.2f}"}

    # 4. Create pending transaction
    reference = _make_reference("EXAM", user.id)
    exam_id   = EXAM_ID_MAP[exam_name]

    transaction = Transaction(
        user_id=user.id, reference=reference, type="exam_pin",
        service_type="exam_pin", amount=selling_price, profit=0.0,
        status="pending", details={"exam_name": exam_name, "exam_id": exam_id, "quantity": quantity},
    )
    db.session.add(transaction)
    db.session.commit()

    # 5. Call VtuNaija
    payload    = {"exam_name": exam_id, "quantity": str(quantity)}
    api_result = vtunaija_request("exam/", data=payload)

    # 6. Handle response
    if _is_success(api_result):
        cost_price    = float(api_result.get("plan_amount", 0) or 0)
        # Fallback: derive cost from margin if VtuNaija didn't return plan_amount
        if cost_price <= 0:
            cost_price = round(selling_price / (1 + PROFIT_MARGIN_EXAM_PIN / 100), 2)
        profit_amount = round(selling_price - cost_price, 2)

        user.wallet_balance -= selling_price
        transaction.status = "success"
        transaction.profit = profit_amount
        transaction.details.update({
            "cost_price":          round(cost_price, 2),
            "pin":                 api_result.get("pin"),
            "serial":              api_result.get("serial"),
            "api_transaction_id":  api_result.get("id"),
        })
        db.session.add(Profit(
            transaction_id=transaction.id, user_id=user.id,
            category="exam_pin", amount=profit_amount,
        ))
        record_purchase(transaction)
        db.session.commit()

        logger.info(f"[ExamPIN] SUCCESS {reference} | profit=₦{profit_amount}")
        return {
            "status": "success",
            "message": f"{exam_name} PIN purchase successful",
            "data": {
                "reference":          reference,
                "transaction_id":     api_result.get("id"),
                "pin":                api_result.get("pin"),
                "serial":             api_result.get("serial"),
                "profit_amount":      profit_amount,
                "selling_price":      selling_price,
                "new_balance":        round(user.wallet_balance, 2),
            },
        }
    else:
        error_msg = api_result.get("api_response") or api_result.get("message") or "Exam PIN purchase failed"
        transaction.status = "failed"
        transaction.details["error"] = error_msg
        db.session.commit()
        logger.error(f"[ExamPIN] FAILED {reference} | {error_msg}")
        return {"status": "error", "message": error_msg}
