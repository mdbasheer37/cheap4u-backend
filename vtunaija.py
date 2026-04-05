import os
import requests
from models import db, User, Transaction, Profit

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

def buy_exam_pin(exam_name, quantity, user_email):
    """
    Purchase exam PIN from VtuNaija.
    exam_name: e.g., "WAEC", "NECO", "JAMB", "NABTEB"
    quantity: integer (1-10)
    user_email: for transaction record
    """
    # Prepare request payload
    payload = {
        "exam_name": exam_name,
        "quantity": str(quantity)
    }
    
    result = vtunaija_request("exam/", data=payload)
    
    # Check for success (both "Status" and "status" fields)
    if result.get("Status") == "successful" or result.get("status") == "success":
        # Extract cost price from API response (plan_amount)
        cost_price = float(result.get("plan_amount", 0))
        # Selling price comes from frontend (stored in exam_pin_prices in app)
        # We need to get selling price from the request; for now we'll assume the frontend sends it.
        # But the backend doesn't have the selling price here.
        # Better: frontend sends selling_price in the request.
        # We'll modify the route to accept selling_price.
        # For now, we'll calculate selling_price from cost_price + profit.
        selling_price = cost_price * (1 + PROFIT_MARGIN_EXAM_PIN / 100)
        profit_amount = selling_price - cost_price
        
        # Get user
        user = User.query.filter_by(email=user_email).first()
        
        # Create transaction record
        transaction = Transaction(
            user_id=user.id if user else None,
            reference=result.get("id") or result.get("ident"),
            type="exam_pin",
            service_type="exam_pin",
            amount=selling_price,
            profit=profit_amount,
            status="success",
            details={
                "exam_name": exam_name,
                "quantity": quantity,
                "pin": result.get("pin"),
                "serial": result.get("serial"),
                "cost_price": cost_price,
                "transaction_id": result.get("id")
            }
        )
        db.session.add(transaction)
        
        if user:
            profit = Profit(
                transaction_id=transaction.id,
                user_id=user.id,
                category="exam_pin",
                amount=profit_amount
            )
            db.session.add(profit)
            # Deduct from user wallet (selling price)
            if user.wallet_balance >= selling_price:
                user.wallet_balance -= selling_price
        db.session.commit()
        
        return {
            "status": "success",
            "message": "Exam PIN purchase successful",
            "data": {
                "transaction_id": result.get("id"),
                "pin": result.get("pin"),
                "serial": result.get("serial"),
                "profit_amount": profit_amount,
                "selling_price": selling_price
            }
        }
    else:
        error_msg = result.get("api_response", "Exam PIN purchase failed")
        return {"status": "error", "message": error_msg}
