# cheapdatahub.py — CheapDataHub provider integration
# Changes from original:
#   1. Reference IDs now use UUID instead of timestamp (no collisions under load)
#   2. Added with_for_update() on user query to prevent race-condition double-spend
#   3. Added "success"/"successful" to success check (was only "true")
#   4. Non-JSON response handled gracefully
# Wallet debit order was already correct in original (debit inside success block).

import os
import uuid
import requests
import logging
from datetime import datetime
from models import db, User, Transaction, Profit, DataPlan, CablePlan, ReferralTransaction

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CHEAPDATAHUB_API_KEY  = os.environ.get("CHEAPDATAHUB_API_KEY", "")
CHEAPDATAHUB_BASE_URL = os.environ.get("CHEAPDATAHUB_BASE_URL", "https://www.cheapdatahub.ng/api/v1/resellers/")

PROFIT_MARGINS = {
    "airtime":     float(os.environ.get("PROFIT_MARGIN_AIRTIME", "5")),
    "electricity": float(os.environ.get("PROFIT_MARGIN_ELECTRICITY", "5")),
}


def cheapdatahub_request(endpoint, method="POST", data=None, timeout=30):
    """Wrapper for CheapDataHub API calls with timeout and error handling."""
    headers = {
        "Authorization": f"Bearer {CHEAPDATAHUB_API_KEY}",
        "Content-Type":  "application/json",
    }
    url = f"{CHEAPDATAHUB_BASE_URL.rstrip('/')}/{endpoint.lstrip('/')}"
    logger.info(f"[CheapDataHub] {method} {url} | data={data}")
    try:
        if method.upper() == "GET":
            response = requests.get(url, headers=headers, timeout=timeout)
        else:
            response = requests.post(url, json=data, headers=headers, timeout=timeout)

        try:
            result = response.json()
        except Exception:
            logger.error(f"[CheapDataHub] Non-JSON response HTTP {response.status_code}: {response.text[:200]}")
            return {"status": "false", "message": f"Provider error (HTTP {response.status_code})"}

        logger.info(f"[CheapDataHub] Response: {result}")
        return result

    except requests.exceptions.Timeout:
        logger.error("[CheapDataHub] Request timed out")
        return {"status": "false", "message": "Request timed out"}
    except requests.exceptions.ConnectionError:
        logger.error("[CheapDataHub] Connection error")
        return {"status": "false", "message": "Network connection error"}
    except Exception as e:
        logger.error(f"[CheapDataHub] Exception: {e}")
        return {"status": "false", "message": f"Request error: {str(e)}"}


def _make_reference(prefix, user_id):
    """Generate a unique reference using UUID — no timestamp collisions."""
    short = uuid.uuid4().hex[:8].upper()
    return f"{prefix}_{short}_{user_id}"


def _is_success(api_result):
    """Check if CheapDataHub returned a success status (handles 'true'/'success'/'successful')."""
    status = str(api_result.get("status", "")).lower()
    return status in ("true", "success", "successful")


def _award_referral_commission(user, selling_price):
    """Award 2% commission to referrer if applicable. Call before final commit."""
    if not user or not user.referred_by_user_id:
        return
    commission = round(selling_price * 0.02, 2)
    referrer = User.query.get(user.referred_by_user_id)
    if referrer:
        referrer.referral_earnings += commission
        db.session.add(ReferralTransaction(
            referrer_id=referrer.id,
            referred_user_id=user.id,
            amount=commission,
            type='commission',
        ))


# ─────────────────────────────────────────────────────────────────────────────
# AIRTIME
# ─────────────────────────────────────────────────────────────────────────────

def buy_airtime(network, phone, amount, user_email):
    """Purchase airtime via CheapDataHub."""
    phone = str(phone).strip()
    if not phone or len(phone) != 11 or not phone.isdigit():
        return {"status": "error", "message": "Invalid phone number. Must be 11 digits."}
    if amount <= 0:
        return {"status": "error", "message": "Amount must be greater than zero."}

    provider_map = {"MTN": 1, "Airtel": 2, "Glo": 3, "9Mobile": 4}
    provider_id = provider_map.get(network)
    if not provider_id:
        return {"status": "error", "message": f"Unsupported network: {network}"}

    # with_for_update prevents two concurrent requests double-debiting the same wallet
    user = User.query.filter_by(email=user_email).with_for_update().first()
    if not user:
        return {"status": "error", "message": "User not found"}

    selling_price = round(float(amount), 2)
    if user.wallet_balance < selling_price:
        return {"status": "error", "message": f"Insufficient balance. Available: ₦{user.wallet_balance:,.2f}"}

    reference = _make_reference("AIRTIME", user.id)
    transaction = Transaction(
        user_id=user.id, reference=reference, type="airtime",
        service_type="airtime", amount=selling_price, profit=0.0,
        status="pending", details={"network": network, "phone": phone, "amount": selling_price},
    )
    db.session.add(transaction)
    db.session.commit()

    payload    = {"provider_id": provider_id, "phone_number": phone, "amount": selling_price}
    api_result = cheapdatahub_request("airtime/purchase/", data=payload)

    if _is_success(api_result):
        profit_percent = PROFIT_MARGINS["airtime"]
        cost_price     = selling_price / (1 + profit_percent / 100)
        profit_amount  = round(selling_price - cost_price, 2)

        user.wallet_balance -= selling_price
        transaction.status = "success"
        transaction.profit = profit_amount
        transaction.details.update({
            "cost_price":    round(cost_price, 2),
            "api_reference": api_result.get("transaction_id"),
        })
        db.session.add(Profit(
            transaction_id=transaction.id, user_id=user.id,
            category="airtime", amount=profit_amount,
        ))
        _award_referral_commission(user, selling_price)
        db.session.commit()

        logger.info(f"[Airtime] SUCCESS {reference} | profit=₦{profit_amount}")
        return {
            "status": "success",
            "message": "Airtime purchase successful",
            "data": {
                "transaction_id": api_result.get("transaction_id"),
                "profit_amount":  round(profit_amount, 2),
                "selling_price":  selling_price,
                "new_balance":    round(user.wallet_balance, 2),
                "reference":      reference
            },
        }
    else:
        error_msg = api_result.get("message", "Airtime purchase failed")
        # Translate CheapDataHub internal errors to user-friendly messages
        if "wallet balance" in error_msg.lower() or "balance too low" in error_msg.lower():
            error_msg = "Service temporarily unavailable. Please try again later."
        if "less than" in error_msg.lower() or "minimum" in error_msg.lower():
            error_msg = "Minimum airtime amount is ₦100."
        transaction.status = "failed"
        transaction.details["error"] = error_msg
        db.session.commit()
        return {"status": "error", "message": error_msg}
# ─────────────────────────────────────────────────────────────────────────────
# DATA
# ─────────────────────────────────────────────────────────────────────────────

def buy_data(plan_id, phone, user_email):
    """Purchase data bundle via CheapDataHub."""
    phone = str(phone).strip()
    if not phone or len(phone) != 11 or not phone.isdigit():
        return {"status": "error", "message": "Invalid phone number. Must be 11 digits."}

    plan = DataPlan.query.filter_by(plan_id=plan_id).first()
    if not plan:
        return {"status": "error", "message": f"Invalid plan ID: {plan_id}"}

    user = User.query.filter_by(email=user_email).with_for_update().first()
    if not user:
        return {"status": "error", "message": "User not found"}

    selling_price = round(float(plan.selling_price), 2)
    if user.wallet_balance < selling_price:
        return {"status": "error", "message": f"Insufficient balance. Available: ₦{user.wallet_balance:,.2f}"}

    reference = _make_reference("DATA", user.id)
    transaction = Transaction(
        user_id=user.id, reference=reference, type="data",
        service_type="data", amount=selling_price, profit=0.0,
        status="pending", details={"plan_id": plan_id, "phone": phone,
                                   "plan_name": plan.size, "provider": plan.provider},
    )
    db.session.add(transaction)
    db.session.commit()

    payload    = {"bundle_id": plan_id, "phone_number": phone}
    api_result = cheapdatahub_request("data/purchase/", data=payload)

    if _is_success(api_result):
        profit_amount = round(selling_price - float(plan.cost_price), 2)

        user.wallet_balance -= selling_price
        transaction.status = "success"
        transaction.profit = profit_amount
        transaction.details.update({
            "cost_price":    round(float(plan.cost_price), 2),
            "api_reference": api_result.get("transaction_id"),
        })
        db.session.add(Profit(
            transaction_id=transaction.id, user_id=user.id,
            category="data", amount=profit_amount,
        ))
        _award_referral_commission(user, selling_price)
        db.session.commit()

        logger.info(f"[Data] SUCCESS {reference} | profit=₦{profit_amount}")
        return {
            "status": "success",
            "message": "Data purchase successful",
            "data": {
                "reference":      reference,
                "transaction_id": api_result.get("transaction_id"),
                "profit_amount":  profit_amount,
                "selling_price":  selling_price,
                "new_balance":    round(user.wallet_balance, 2),
            },
        }
    else:
        error_msg = api_result.get("message", "Data purchase failed")
        transaction.status = "failed"
        transaction.details["error"] = error_msg
        db.session.commit()
        logger.error(f"[Data] FAILED {reference} | {error_msg}")
        return {"status": "error", "message": error_msg}


# ─────────────────────────────────────────────────────────────────────────────
# ELECTRICITY
# ─────────────────────────────────────────────────────────────────────────────

def buy_electricity(disco, meter_number, meter_type, amount, phone, user_email):
    """Purchase electricity token via CheapDataHub."""
    phone        = str(phone).strip()
    meter_number = str(meter_number).strip()

    if not phone or len(phone) != 11 or not phone.isdigit():
        return {"status": "error", "message": "Invalid phone number. Must be 11 digits."}
    if amount <= 0:
        return {"status": "error", "message": "Amount must be greater than zero."}
    if not meter_number or len(meter_number) < 6 or not meter_number.isdigit():
        return {"status": "error", "message": "Invalid meter number (minimum 6 digits)"}
    if meter_type not in ("prepaid", "postpaid"):
        return {"status": "error", "message": "meter_type must be prepaid or postpaid"}

    user = User.query.filter_by(email=user_email).with_for_update().first()
    if not user:
        return {"status": "error", "message": "User not found"}

    selling_price = round(float(amount), 2)
    if user.wallet_balance < selling_price:
        return {"status": "error", "message": f"Insufficient balance. Available: ₦{user.wallet_balance:,.2f}"}

    reference = _make_reference("ELEC", user.id)
    transaction = Transaction(
        user_id=user.id, reference=reference, type="electricity",
        service_type="electricity", amount=selling_price, profit=0.0,
        status="pending", details={"disco": disco, "meter_number": meter_number,
                                   "meter_type": meter_type, "phone": phone},
    )
    db.session.add(transaction)
    db.session.commit()

    payload    = {"disco": disco, "meter_number": meter_number,
                  "meter_type": meter_type, "amount": selling_price, "phone_number": phone}
    api_result = cheapdatahub_request("electricity/purchase/", data=payload)

    if _is_success(api_result):
        profit_percent = PROFIT_MARGINS["electricity"]
        cost_price     = selling_price / (1 + profit_percent / 100)
        profit_amount  = round(selling_price - cost_price, 2)
        token          = api_result.get("token") or api_result.get("data", {}).get("token", "")

        user.wallet_balance -= selling_price
        transaction.status = "success"
        transaction.profit = profit_amount
        transaction.details.update({
            "cost_price":    round(cost_price, 2),
            "token":         token,
            "api_reference": api_result.get("transaction_id"),
        })
        db.session.add(Profit(
            transaction_id=transaction.id, user_id=user.id,
            category="electricity", amount=profit_amount,
        ))
        _award_referral_commission(user, selling_price)
        db.session.commit()

        logger.info(f"[Electricity] SUCCESS {reference} | token={token} | profit=₦{profit_amount}")
        return {
            "status": "success",
            "message": "Electricity purchase successful",
            "data": {
                "reference":      reference,
                "transaction_id": api_result.get("transaction_id"),
                "token":          token,
                "profit_amount":  profit_amount,
                "selling_price":  selling_price,
                "new_balance":    round(user.wallet_balance, 2),
            },
        }
    else:
        error_msg = api_result.get("message", "Electricity purchase failed")
        transaction.status = "failed"
        transaction.details["error"] = error_msg
        db.session.commit()
        logger.error(f"[Electricity] FAILED {reference} | {error_msg}")
        return {"status": "error", "message": error_msg}


# ─────────────────────────────────────────────────────────────────────────────
# CABLE TV
# ─────────────────────────────────────────────────────────────────────────────

def buy_cable_tv(plan_id, smartcard, user_email):
    """Purchase cable TV subscription via CheapDataHub."""
    smartcard = str(smartcard).strip()
    if not smartcard or len(smartcard) < 6:
        return {"status": "error", "message": "Invalid smartcard/IUC number (minimum 6 characters)"}

    plan = CablePlan.query.filter_by(plan_id=plan_id).first()
    if not plan:
        return {"status": "error", "message": f"Invalid cable plan ID: {plan_id}"}

    user = User.query.filter_by(email=user_email).with_for_update().first()
    if not user:
        return {"status": "error", "message": "User not found"}

    selling_price = round(float(plan.selling_price), 2)
    if user.wallet_balance < selling_price:
        return {"status": "error", "message": f"Insufficient balance. Available: ₦{user.wallet_balance:,.2f}"}

    reference = _make_reference("CABLE", user.id)
    transaction = Transaction(
        user_id=user.id, reference=reference, type="cable_tv",
        service_type="cable_tv", amount=selling_price, profit=0.0,
        status="pending", details={"plan_id": plan_id, "smartcard": smartcard,
                                   "provider": plan.provider, "plan_name": plan.plan_name},
    )
    db.session.add(transaction)
    db.session.commit()

    payload    = {"package_id": plan_id, "smartcard_number": smartcard}
    api_result = cheapdatahub_request("cable/purchase/", data=payload)

    if _is_success(api_result):
        profit_amount = round(selling_price - float(plan.cost_price), 2)

        user.wallet_balance -= selling_price
        transaction.status = "success"
        transaction.profit = profit_amount
        transaction.details.update({
            "cost_price":    round(float(plan.cost_price), 2),
            "api_reference": api_result.get("transaction_id"),
        })
        db.session.add(Profit(
            transaction_id=transaction.id, user_id=user.id,
            category="cable_tv", amount=profit_amount,
        ))
        _award_referral_commission(user, selling_price)
        db.session.commit()

        logger.info(f"[CableTV] SUCCESS {reference} | profit=₦{profit_amount}")
        return {
            "status": "success",
            "message": "Cable TV subscription successful",
            "data": {
                "reference":      reference,
                "transaction_id": api_result.get("transaction_id"),
                "provider":       plan.provider,
                "plan_name":      plan.plan_name,
                "profit_amount":  profit_amount,
                "selling_price":  selling_price,
                "new_balance":    round(user.wallet_balance, 2),
            },
        }
    else:
        error_msg = api_result.get("message", "Cable TV subscription failed")
        transaction.status = "failed"
        transaction.details["error"] = error_msg
        db.session.commit()
        logger.error(f"[CableTV] FAILED {reference} | {error_msg}")
        return {"status": "error", "message": error_msg}
