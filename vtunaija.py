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

def buy_exam_pin(exam_name, quantity, user_email, selling_price=None):
    """
    Purchase exam PIN from VtuNaija.
    
    Parameters:
    - exam_name: e.g., "WAEC", "NECO", "JAMB", "NABTEB" (use ID: 2 for WAEC, etc.)
    - quantity: integer (1-10)
    - user_email: email of user making purchase
    - selling_price: optional - price customer paid (if None, calculated from cost + profit)
    
    Returns:
    - dict with status, message, and data (pin, serial, profit_amount, selling_price)
    """
    
    # Step 1: Validate exam_name mapping
    # VtuNaija expects exam_name as ID (e.g., "2" for WAEC)
    exam_name_map = {
        "WAEC": "2",
        "NECO": "3", 
        "JAMB": "1",
        "NABTEB": "4"
    }
    
    # If exam_name is a string like "WAEC", convert to ID
    if exam_name in exam_name_map:
        exam_id = exam_name_map[exam_name]
    else:
        exam_id = str(exam_name)  # Assume it's already an ID
    
    # Step 2: Prepare request payload
    payload = {
        "exam_name": exam_id,
        "quantity": str(quantity)
    }
    
    # Step 3: Make API request to VtuNaija
    result = vtunaija_request("exam/", data=payload)
    
    # Step 4: Check if request was successful
    if result.get("Status") == "successful" or result.get("status") == "success":
        
        # Extract cost price from API response (what VtuNaija charges you)
        cost_price = float(result.get("plan_amount", 0))
        
        # Step 5: Calculate selling price and profit
        if selling_price is None:
            # If frontend didn't send selling price, calculate from cost + profit margin
            selling_price = cost_price * (1 + PROFIT_MARGIN_EXAM_PIN / 100)
        else:
            # Use selling price from frontend
            selling_price = float(selling_price)
        
        profit_amount = selling_price - cost_price
        
        # Step 6: Get user from database
        user = User.query.filter_by(email=user_email).first()
        
        if not user:
            return {
                "status": "error",
                "message": "User not found"
            }
        
        # Step 7: Check if user has sufficient wallet balance
        if user.wallet_balance < selling_price:
            return {
                "status": "error",
                "message": f"Insufficient wallet balance. Available: ₦{user.wallet_balance:,.2f}, Required: ₦{selling_price:,.2f}"
            }
  def buy_exam_pin(exam_name, quantity, user_email, selling_price=None):
    """
    Purchase exam PIN from VtuNaija.
    
    VtuNaija Cost Prices:
    - WAEC: ₦3,450
    - NECO: ₦2,350
    - NABTEB: ₦900
    - JAMB: ₦15,000
    """
    
    # VtuNaija exam ID mapping (confirm these IDs with their API)
    exam_name_map = {
        "WAEC": "2",      # Verify with VtuNaija docs
        "NECO": "3",      # Verify with VtuNaija docs
        "NABTEB": "4",    # Verify with VtuNaija docs
        "JAMB": "1"       # Verify with VtuNaija docs
    }
    
    # Alternative mapping if VtuNaija uses different IDs
    # Based on typical VTU platforms:
    # 1 = JAMB, 2 = WAEC, 3 = NECO, 4 = NABTEB
    
    if exam_name in exam_name_map:
        exam_id = exam_name_map[exam_name]
    else:
        exam_id = str(exam_name)  # Assume it's already an ID
    
    # Prepare request payload
    payload = {
        "exam_name": exam_id,
        "quantity": str(quantity)
    }
    
    # Make API request to VtuNaija
    result = vtunaija_request("exam/", data=payload)
    
    # Check if request was successful
    if result.get("Status") == "successful" or result.get("status") == "success":
        
        # Extract cost price from API response (what VtuNaija charges you)
        cost_price = float(result.get("plan_amount", 0))
        
        # Verify cost price matches our known prices
        expected_cost = {
            "WAEC": 3450,
            "NECO": 2350,
            "NABTEB": 900,
            "JAMB": 15000
        }.get(exam_name, cost_price)
        
        if cost_price != expected_cost:
            print(f"⚠️ Warning: {exam_name} cost price mismatch. Expected: ₦{expected_cost}, Got: ₦{cost_price}")
        
        # Step 5: Calculate selling price and profit
        if selling_price is None:
            # If frontend didn't send selling price, use default from frontend prices
            # This should not happen because frontend always sends selling_price
            default_prices = {
                "WAEC": 4000,
                "NECO": 3000,
                "NABTEB": 1500,
                "JAMB": 18000
            }
            selling_price = default_prices.get(exam_name, cost_price * 1.1)
        else:
            selling_price = float(selling_price)
        
        profit_amount = selling_price - cost_price
        
        # Validate profit is positive
        if profit_amount <= 0:
            print(f"⚠️ Warning: Negative profit for {exam_name}. Cost: ₦{cost_price}, Selling: ₦{selling_price}")
        
        # Get user from database
        user = User.query.filter_by(email=user_email).first()
        
        if not user:
            return {
                "status": "error",
                "message": "User not found"
            }
        
        # Check if user has sufficient wallet balance
        if user.wallet_balance < selling_price:
            return {
                "status": "error",
                "message": f"Insufficient wallet balance. Available: ₦{user.wallet_balance:,.2f}, Required: ₦{selling_price:,.2f}"
            }
        
        # Create transaction record
        transaction = Transaction(
            user_id=user.id,
            reference=result.get("id") or result.get("ident") or f"EXAM_{datetime.utcnow().timestamp()}",
            type="exam_pin",
            service_type="exam_pin",
            amount=selling_price,
            profit=profit_amount,
            status="success",
            details={
                "exam_name": exam_name,
                "exam_id": exam_id,
                "quantity": quantity,
                "pin": result.get("pin"),
                "serial": result.get("serial"),
                "cost_price": cost_price,
                "selling_price": selling_price,
                "profit_amount": profit_amount,
                "profit_margin_percent": (profit_amount / cost_price) * 100 if cost_price > 0 else 0,
                "api_transaction_id": result.get("id"),
                "api_response": result
            }
        )
        db.session.add(transaction)
        
        # Create profit record
        profit = Profit(
            transaction_id=transaction.id,
            user_id=user.id,
            category="exam_pin",
            amount=profit_amount
        )
        db.session.add(profit)
        
        # Deduct from user's wallet balance
        user.wallet_balance -= selling_price
        
        # Commit all changes to database
        db.session.commit()
        
        # Return success response
        return {
            "status": "success",
            "message": f"{exam_name} PIN purchase successful",
            "data": {
                "transaction_id": result.get("id") or result.get("ident"),
                "pin": result.get("pin"),
                "serial": result.get("serial"),
                "profit_amount": profit_amount,
                "selling_price": selling_price,
                "cost_price": cost_price,
                "quantity": quantity,
                "exam_name": exam_name,
                "profit_margin_percent": round((profit_amount / cost_price) * 100, 2) if cost_price > 0 else 0
            }
        }
    
    else:
        # Handle failed API response
        error_msg = result.get("api_response", "Exam PIN purchase failed")
        
        # Still record failed transaction for tracking
        user = User.query.filter_by(email=user_email).first()
        if user:
            transaction = Transaction(
                user_id=user.id,
                reference=result.get("id") or result.get("ident") or f"EXAM_FAILED_{datetime.utcnow().timestamp()}",
                type="exam_pin",
                service_type="exam_pin",
                amount=0,
                profit=0,
                status="failed",
                details={
                    "exam_name": exam_name,
                    "quantity": quantity,
                    "error": error_msg,
                    "api_response": result
                }
            )
            db.session.add(transaction)
            db.session.commit()
        
        return {
            "status": "error",
            "message": f"Exam PIN purchase failed: {error_msg}"
        }
