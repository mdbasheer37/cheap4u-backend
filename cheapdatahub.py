# cheapdatahub.py
import os
import requests
import logging
from datetime import datetime
from models import db, User, Transaction, Profit, DataPlan, CablePlan

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CHEAPDATAHUB_API_KEY = os.environ.get("CHEAPDATAHUB_API_KEY", "")
CHEAPDATAHUB_BASE_URL = os.environ.get("CHEAPDATAHUB_BASE_URL", "https://www.cheapdatahub.ng/api/v1/resellers/")

# Profit margins for services without per-plan costs
PROFIT_MARGINS = {
    "airtime": float(os.environ.get("PROFIT_MARGIN_AIRTIME", "5")),
    "electricity": float(os.environ.get("PROFIT_MARGIN_ELECTRICITY", "5")),
}

def cheapdatahub_request(endpoint, method="POST", data=None, timeout=30):
    """Wrapper for CheapDataHub API calls with timeout and error handling."""
    headers = {
        "Authorization": f"Bearer {CHEAPDATAHUB_API_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{CHEAPDATAHUB_BASE_URL}{endpoint}"
    logger.info(f"CheapDataHub Request: {method} {url} | Data: {data}")
    try:
        if method.upper() == "GET":
            response = requests.get(url, headers=headers, timeout=timeout)
        else:
            response = requests.post(url, json=data, headers=headers, timeout=timeout)
        result = response.json()
        logger.info(f"CheapDataHub Response: {result}")
        return result
    except requests.exceptions.Timeout:
        logger.error("CheapDataHub request timed out")
        return {"status": "false", "message": "Request timed out"}
    except requests.exceptions.ConnectionError:
        logger.error("CheapDataHub connection error")
        return {"status": "false", "message": "Network connection error"}
    except Exception as e:
        logger.error(f"CheapDataHub request error: {str(e)}")
        return {"status": "false", "message": f"Request error: {str(e)}"}

# ------------------------------------------------------------------
# AIRTIME
# ------------------------------------------------------------------
def buy_airtime(network, phone, amount, user_email):
    """
    Purchase airtime with wallet validation, pending transaction,
    atomic updates, and proper error handling.
    """
    # 1. Validate inputs
    if not phone or len(str(phone)) != 11 or not str(phone).isdigit():
        return {"status": "error", "message": "Invalid phone number. Must be 11 digits."}
    if amount <= 0:
        return {"status": "error", "message": "Amount must be greater than zero."}
    provider_map = {"MTN": 1, "Airtel": 2, "Glo": 3, "9Mobile": 4}
    provider_id = provider_map.get(network)
    if not provider_id:
        return {"status": "error", "message": "Unsupported network"}

    # 2. Fetch user and validate wallet
    user = User.query.filter_by(email=user_email).first()
    if not user:
        return {"status": "error", "message": "User not found"}

    selling_price = float(amount)
    if user.wallet_balance < selling_price:
        return {"status": "error", "message": f"Insufficient balance. Available: ₦{user.wallet_balance:,.2f}"}

    # 3. Create pending transaction
    reference = f"AIRTIME_{datetime.utcnow().timestamp()}_{user.id}"
    transaction = Transaction(
        user_id=user.id,
        reference=reference,
        type="airtime",
        service_type="airtime",
        amount=selling_price,
        profit=0.0,
        status="pending",
        details={"network": network, "phone": phone, "amount": amount}
    )
    db.session.add(transaction)
    db.session.commit()  # Commit to get transaction.id

    # 4. Call external API
    payload = {"provider_id": provider_id, "phone_number": phone, "amount": amount}
    api_result = cheapdatahub_request("airtime/purchase/", data=payload)

    # 5. Handle API response
    if api_result.get("status") == "true":
        # Success: calculate profit, deduct wallet, update transaction
        profit_percent = PROFIT_MARGINS["airtime"]
        cost_price = selling_price / (1 + profit_percent / 100)
        profit_amount = selling_price - cost_price

        user.wallet_balance -= selling_price
        transaction.status = "success"
        transaction.profit = profit_amount
        transaction.details.update({
            "cost_price": cost_price,
            "api_reference": api_result.get("transaction_id")
        })

        # Create profit record
        profit = Profit(
            transaction_id=transaction.id,
            user_id=user.id,
            category="airtime",
            amount=profit_amount
        )
        db.session.add(profit)
        db.session.commit()
        # inside buy_airtime, after db.session.commit() of transaction and profit

        if user and user.referred_by_user_id:
    # Grant commission (e.g., 2% of selling price)
          commission_rate = 0.02  # 2%
          commission = selling_price * commission_rate
          referrer = User.query.get(user.referred_by_user_id)
          if referrer:
             referrer.referral_earnings += commission
        
        # Log commission
             ref_tx = ReferralTransaction(
               referrer_id=referrer.id,
               referred_user_id=user.id,
               amount=commission,
               type='commission'
        )
              db.session.add(ref_tx)
        # db.session.commit() will be called later or we can commit here? Better to let outer commit handle it. 
        return {
            "status": "success",
            "message": "Airtime purchase successful",
            "data": {
                "transaction_id": api_result.get("transaction_id"),
                "profit_amount": profit_amount,
                "selling_price": selling_price,
                "reference": reference
            }
        }
    else:
        # Failure: do NOT deduct wallet, mark transaction failed
        transaction.status = "failed"
        transaction.details["error"] = api_result.get("message", "Airtime purchase failed")
        db.session.commit()
        return {"status": "error", "message": api_result.get("message", "Airtime purchase failed")}

# ------------------------------------------------------------------
# DATA
# ------------------------------------------------------------------
def buy_data(plan_id, phone, user_email):
    """Purchase data bundle with proper validation and atomic handling."""
    # 1. Validate inputs
    if not phone or len(str(phone)) != 11 or not str(phone).isdigit():
        return {"status": "error", "message": "Invalid phone number. Must be 11 digits."}

    # 2. Fetch plan from database
    plan = DataPlan.query.filter_by(plan_id=plan_id).first()
    if not plan:
        return {"status": "error", "message": "Invalid plan ID"}

    user = User.query.filter_by(email=user_email).first()
    if not user:
        return {"status": "error", "message": "User not found"}

    selling_price = plan.selling_price
    if user.wallet_balance < selling_price:
        return {"status": "error", "message": f"Insufficient balance. Available: ₦{user.wallet_balance:,.2f}"}

    # 3. Create pending transaction
    reference = f"DATA_{datetime.utcnow().timestamp()}_{user.id}"
    transaction = Transaction(
        user_id=user.id,
        reference=reference,
        type="data",
        service_type="data",
        amount=selling_price,
        profit=0.0,
        status="pending",
        details={"plan_id": plan_id, "phone": phone}
    )
    db.session.add(transaction)
    db.session.commit()

    # 4. Call external API
    payload = {"bundle_id": plan_id, "phone_number": phone}
    api_result = cheapdatahub_request("data/purchase/", data=payload)

    # 5. Handle API response
    if api_result.get("status") == "true":
        profit_amount = selling_price - plan.cost_price
        user.wallet_balance -= selling_price
        transaction.status = "success"
        transaction.profit = profit_amount
        transaction.details.update({
            "cost_price": plan.cost_price,
            "api_reference": api_result.get("transaction_id")
        })

        profit = Profit(
            transaction_id=transaction.id,
            user_id=user.id,
            category="data",
            amount=profit_amount
        )
        db.session.add(profit)
        db.session.commit()

        return {
            "status": "success",
            "message": "Data purchase successful",
            "data": {
                "transaction_id": api_result.get("transaction_id"),
                "profit_amount": profit_amount,
                "selling_price": selling_price,
                "reference": reference
            }
        }
    else:
        transaction.status = "failed"
        transaction.details["error"] = api_result.get("message", "Data purchase failed")
        db.session.commit()
        return {"status": "error", "message": api_result.get("message", "Data purchase failed")}

# ------------------------------------------------------------------
# ELECTRICITY
# ------------------------------------------------------------------
def buy_electricity(disco, meter_number, meter_type, amount, phone, user_email):
    """Purchase electricity with proper validation and atomic handling."""
    # 1. Validate inputs
    if not phone or len(str(phone)) != 11 or not str(phone).isdigit():
        return {"status": "error", "message": "Invalid phone number. Must be 11 digits."}
    if amount <= 0:
        return {"status": "error", "message": "Amount must be greater than zero."}
    if not meter_number or len(meter_number) < 6:
        return {"status": "error", "message": "Invalid meter number"}

    user = User.query.filter_by(email=user_email).first()
    if not user:
        return {"status": "error", "message": "User not found"}

    selling_price = float(amount)
    if user.wallet_balance < selling_price:
        return {"status": "error", "message": f"Insufficient balance. Available: ₦{user.wallet_balance:,.2f}"}

    # 2. Create pending transaction
    reference = f"ELEC_{datetime.utcnow().timestamp()}_{user.id}"
    transaction = Transaction(
        user_id=user.id,
        reference=reference,
        type="electricity",
        service_type="electricity",
        amount=selling_price,
        profit=0.0,
        status="pending",
        details={"disco": disco, "meter_number": meter_number, "meter_type": meter_type, "phone": phone}
    )
    db.session.add(transaction)
    db.session.commit()

    # 3. Call external API
    payload = {
        "disco": disco,
        "meter_number": meter_number,
        "meter_type": meter_type,
        "amount": amount,
        "phone_number": phone
    }
    api_result = cheapdatahub_request("electricity/purchase/", data=payload)

    # 4. Handle API response
    if api_result.get("status") == "true":
        profit_percent = PROFIT_MARGINS["electricity"]
        cost_price = selling_price / (1 + profit_percent / 100)
        profit_amount = selling_price - cost_price

        user.wallet_balance -= selling_price
        transaction.status = "success"
        transaction.profit = profit_amount
        transaction.details.update({
            "cost_price": cost_price,
            "token": api_result.get("token"),
            "api_reference": api_result.get("transaction_id")
        })

        profit = Profit(
            transaction_id=transaction.id,
            user_id=user.id,
            category="electricity",
            amount=profit_amount
        )
        db.session.add(profit)
        db.session.commit()

        return {
            "status": "success",
            "message": "Electricity purchase successful",
            "data": {
                "transaction_id": api_result.get("transaction_id"),
                "token": api_result.get("token"),
                "profit_amount": profit_amount,
                "selling_price": selling_price,
                "reference": reference
            }
        }
    else:
        transaction.status = "failed"
        transaction.details["error"] = api_result.get("message", "Electricity purchase failed")
        db.session.commit()
        return {"status": "error", "message": api_result.get("message", "Electricity purchase failed")}

# ------------------------------------------------------------------
# CABLE TV
# ------------------------------------------------------------------
def buy_cable_tv(plan_id, smartcard, user_email):
    """Purchase cable TV subscription with proper validation and atomic handling."""
    # 1. Validate inputs
    if not smartcard or len(smartcard) < 6:
        return {"status": "error", "message": "Invalid smartcard/IUC number"}

    # 2. Fetch plan from database
    plan = CablePlan.query.filter_by(plan_id=plan_id).first()
    if not plan:
        return {"status": "error", "message": "Invalid cable plan ID"}

    user = User.query.filter_by(email=user_email).first()
    if not user:
        return {"status": "error", "message": "User not found"}

    selling_price = plan.selling_price
    if user.wallet_balance < selling_price:
        return {"status": "error", "message": f"Insufficient balance. Available: ₦{user.wallet_balance:,.2f}"}

    # 3. Create pending transaction
    reference = f"CABLE_{datetime.utcnow().timestamp()}_{user.id}"
    transaction = Transaction(
        user_id=user.id,
        reference=reference,
        type="cable_tv",
        service_type="cable_tv",
        amount=selling_price,
        profit=0.0,
        status="pending",
        details={"plan_id": plan_id, "smartcard": smartcard}
    )
    db.session.add(transaction)
    db.session.commit()

    # 4. Call external API
    payload = {"package_id": plan_id, "smartcard_number": smartcard}
    api_result = cheapdatahub_request("cable/purchase/", data=payload)

    # 5. Handle API response
    if api_result.get("status") == "true":
        profit_amount = selling_price - plan.cost_price
        user.wallet_balance -= selling_price
        transaction.status = "success"
        transaction.profit = profit_amount
        transaction.details.update({
            "cost_price": plan.cost_price,
            "api_reference": api_result.get("transaction_id")
        })

        profit = Profit(
            transaction_id=transaction.id,
            user_id=user.id,
            category="cable_tv",
            amount=profit_amount
        )
        db.session.add(profit)
        db.session.commit()

        return {
            "status": "success",
            "message": "Cable TV subscription successful",
            "data": {
                "transaction_id": api_result.get("transaction_id"),
                "profit_amount": profit_amount,
                "selling_price": selling_price,
                "reference": reference
            }
        }
    else:
        transaction.status = "failed"
        transaction.details["error"] = api_result.get("message", "Cable TV subscription failed")
        db.session.commit()
        return {"status": "error", "message": api_result.get("message", "Cable TV subscription failed")}
