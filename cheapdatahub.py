# cheapdatahub.py
# Fixed issues (based on official CheapDataHub API docs):
#   1. Airtime:     added new_balance to success response
#   2. Electricity: changed payload field "disco" → "disco_id" (integer)
#                   changed "phone_number" → "phone"
#                   token is nested inside data.token not top level
#   3. Cable TV:    changed "package_id" → "plan_id"
#                   changed "smartcard_number" → "cardnumber"
#                   added "phone" field (required by API)
#   4. All:         status check now handles "true", "True", "success"

import os
import uuid
import requests
import logging
from datetime import datetime
from models import db, User, Transaction, Profit, DataPlan, CablePlan, ReferralTransaction
from challenge import record_purchase
from cashback import award_cashback
import coupon as coupon_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CHEAPDATAHUB_API_KEY  = os.environ.get("CHEAPDATAHUB_API_KEY", "")
CHEAPDATAHUB_BASE_URL = os.environ.get("CHEAPDATAHUB_BASE_URL",
                                        "https://www.cheapdatahub.ng/api/v1/resellers/")
PROFIT_MARGINS = {
    "airtime":     float(os.environ.get("PROFIT_MARGIN_AIRTIME", "5")),
    "electricity": float(os.environ.get("PROFIT_MARGIN_ELECTRICITY", "5")),
}

# ── Electricity disco name → ID mapping (from CheapDataHub dashboard) ─────────
DISCO_ID_MAP = {
    "Ikeja Electric":          1,
    "IKEDC":                   1,
    "Eko Electric":            2,
    "EKEDC":                   2,
    "Ibadan Electric":         3,
    "IBEDC":                   3,
    "Enugu Electric":          4,
    "EEDC":                    4,
    "Abuja Electric AEDC":     5,
    "AEDC":                    5,
    "Kaduna Electric":         6,
    "KEDCO":                   6,
    "Port Harcourt Electric":  7,
    "PHED":                    7,
    "Jos Electricity JEDplc":  8,
    "JED":                     8,
    "Kano Electric":           9,
    "KEDCO Kano":              9,
    "Benin Electric":          10,
    "BEDC":                    10,
}


def cheapdatahub_request(endpoint, method="POST", data=None, timeout=30):
    """Wrapper for CheapDataHub API calls."""
    headers = {
        "Authorization": f"Bearer {CHEAPDATAHUB_API_KEY}",
        "Content-Type":  "application/json",
    }
    url = f"{CHEAPDATAHUB_BASE_URL}{endpoint}"
    logger.info(f"CheapDataHub Request: {method} {url} | Data: {data}")
    try:
        if method.upper() == "GET":
            response = requests.get(url, headers=headers, timeout=timeout)
        else:
            response = requests.post(url, json=data, headers=headers, timeout=timeout)
        try:
            result = response.json()
        except Exception:
            logger.error(f"Non-JSON response HTTP {response.status_code}: {response.text[:200]}")
            return {"status": "false", "message": f"Provider error (HTTP {response.status_code})"}
        logger.info(f"CheapDataHub Response: {result}")
        return result
    except requests.exceptions.Timeout:
        return {"status": "false", "message": "Request timed out"}
    except requests.exceptions.ConnectionError:
        return {"status": "false", "message": "Network connection error"}
    except Exception as e:
        return {"status": "false", "message": f"Request error: {str(e)}"}


def _is_success(api_result):
    """Handle 'true', 'True', 'success', 'successful' from CheapDataHub."""
    status = str(api_result.get("status", "")).lower()
    return status in ("true", "success", "successful")


def _make_reference(prefix, user_id):
    """UUID-based reference — no timestamp collisions."""
    return f"{prefix}_{uuid.uuid4().hex[:8].upper()}_{user_id}"


def _award_referral_commission(user, selling_price):
    if not user or not user.referred_by_user_id:
        return
    commission = selling_price * 0.02
    referrer = User.query.get(user.referred_by_user_id)
    if referrer:
        referrer.referral_earnings += commission
        db.session.add(ReferralTransaction(
            referrer_id=referrer.id,
            referred_user_id=user.id,
            amount=commission,
            type='commission'
        ))


# ── AIRTIME ────────────────────────────────────────────────────────────────────
def buy_airtime(network, phone, amount, user_email, coupon_code=None):
    if not phone or len(str(phone)) != 11 or not str(phone).isdigit():
        return {"status": "error", "message": "Invalid phone number. Must be 11 digits."}
    if amount <= 0:
        return {"status": "error", "message": "Amount must be greater than zero."}

    # CheapDataHub provider IDs (confirmed from their API docs)
    provider_map = {"MTN": 1, "Glo": 2, "Airtel": 3, "9Mobile": 4}
    provider_id  = provider_map.get(network)
    if not provider_id:
        return {"status": "error", "message": f"Unsupported network: {network}"}

    user = User.query.filter_by(email=user_email).first()
    if not user:
        return {"status": "error", "message": "User not found"}

    selling_price = float(amount)

    discount, applied_coupon, coupon_error = coupon_service.validate_and_price(
        user, coupon_code, "airtime", selling_price)
    if coupon_error:
        return {"status": "error", "message": coupon_error}
    charge_amount = round(selling_price - (discount or 0), 2)

    if user.wallet_balance < charge_amount:
        return {"status": "error",
                "message": f"Insufficient balance. Available: ₦{user.wallet_balance:,.2f}"}

    reference = _make_reference("AIRTIME", user.id)
    transaction = Transaction(
        user_id=user.id, reference=reference, type="airtime",
        service_type="airtime", amount=charge_amount, profit=0.0,
        status="pending",
        details={"network": network, "phone": phone, "amount": amount,
                 "selling_price": selling_price, "coupon_code": applied_coupon.code if applied_coupon else None,
                 "discount_amount": discount or 0}
    )
    db.session.add(transaction)
    db.session.commit()

    # Exact payload from CheapDataHub docs
    payload    = {"provider_id": provider_id, "phone_number": phone, "amount": selling_price}
    api_result = cheapdatahub_request("airtime/purchase/", data=payload)

    if _is_success(api_result):
        profit_percent = PROFIT_MARGINS["airtime"]
        cost_price     = selling_price / (1 + profit_percent / 100)
        profit_amount  = round(charge_amount - cost_price, 2)
        user.wallet_balance = round(user.wallet_balance - charge_amount, 2)
        transaction.status = "success"
        transaction.profit = profit_amount
        transaction.details.update({
            "cost_price":    round(cost_price, 2),
            "api_reference": api_result.get("reference") or api_result.get("transaction_id"),
        })
        db.session.add(Profit(
            transaction_id=transaction.id, user_id=user.id,
            category="airtime", amount=profit_amount
        ))
        _award_referral_commission(user, charge_amount)
        if applied_coupon:
            coupon_service.redeem_coupon(applied_coupon, user, transaction, discount, category="airtime")
        record_purchase(transaction)
        award_cashback(transaction)
        db.session.commit()
        return {
            "status":  "success",
            "message": "Airtime purchase successful",
            "data": {
                "reference":      reference,
                "api_reference":  api_result.get("reference"),
                "profit_amount":  profit_amount,
                "selling_price":  selling_price,
                "discount_amount": discount or 0,
                "amount_charged": charge_amount,
                "new_balance":    round(user.wallet_balance, 2),  # FIX: added
            }
        }
    else:
        error_msg = api_result.get("message", "Airtime purchase failed")
        if "wallet" in error_msg.lower() or "balance" in error_msg.lower():
            error_msg = "Service temporarily unavailable. Please try again later."
        transaction.status = "failed"
        transaction.details["error"] = error_msg
        db.session.commit()
        return {"status": "error", "message": error_msg}


# ── DATA ───────────────────────────────────────────────────────────────────────
def buy_data(plan_id, phone, user_email, coupon_code=None):
    if not phone or len(str(phone)) != 11 or not str(phone).isdigit():
        return {"status": "error", "message": "Invalid phone number. Must be 11 digits."}

    plan = DataPlan.query.filter_by(plan_id=plan_id).first()
    if not plan:
        return {"status": "error", "message": "Invalid plan ID"}

    user = User.query.filter_by(email=user_email).first()
    if not user:
        return {"status": "error", "message": "User not found"}

    selling_price = float(plan.selling_price)

    discount, applied_coupon, coupon_error = coupon_service.validate_and_price(
        user, coupon_code, "data", selling_price)
    if coupon_error:
        return {"status": "error", "message": coupon_error}
    charge_amount = round(selling_price - (discount or 0), 2)

    if user.wallet_balance < charge_amount:
        return {"status": "error",
                "message": f"Insufficient balance. Available: ₦{user.wallet_balance:,.2f}"}

    reference = _make_reference("DATA", user.id)
    transaction = Transaction(
        user_id=user.id, reference=reference, type="data",
        service_type="data", amount=charge_amount, profit=0.0,
        status="pending",
        details={"plan_id": plan_id, "phone": phone,
                 "plan_name": plan.size, "provider": plan.provider,
                 "selling_price": selling_price, "coupon_code": applied_coupon.code if applied_coupon else None,
                 "discount_amount": discount or 0}
    )
    db.session.add(transaction)
    db.session.commit()

    # Exact payload from CheapDataHub docs
    payload    = {"bundle_id": plan_id, "phone_number": phone}
    api_result = cheapdatahub_request("data/purchase/", data=payload)

    if _is_success(api_result):
        profit_amount = round(charge_amount - float(plan.cost_price), 2)
        user.wallet_balance = round(user.wallet_balance - charge_amount, 2)
        transaction.status = "success"
        transaction.profit = profit_amount
        transaction.details.update({
            "cost_price":    float(plan.cost_price),
            "api_reference": api_result.get("reference") or api_result.get("transaction_id"),
        })
        db.session.add(Profit(
            transaction_id=transaction.id, user_id=user.id,
            category="data", amount=profit_amount
        ))
        _award_referral_commission(user, charge_amount)
        if applied_coupon:
            coupon_service.redeem_coupon(applied_coupon, user, transaction, discount, category="data")
        record_purchase(transaction)
        award_cashback(transaction)
        db.session.commit()
        return {
            "status":  "success",
            "message": "Data purchase successful",
            "data": {
                "reference":     reference,
                "api_reference": api_result.get("reference"),
                "profit_amount": profit_amount,
                "selling_price": selling_price,
                "discount_amount": discount or 0,
                "amount_charged": charge_amount,
                "new_balance":   round(user.wallet_balance, 2),
            }
        }
    else:
        error_msg = api_result.get("message", "Data purchase failed")
        transaction.status = "failed"
        transaction.details["error"] = error_msg
        db.session.commit()
        return {"status": "error", "message": error_msg}


# ── ELECTRICITY ────────────────────────────────────────────────────────────────
def buy_electricity(disco, meter_number, meter_type, amount, phone, user_email, coupon_code=None):
    if not phone or len(str(phone)) != 11 or not str(phone).isdigit():
        return {"status": "error", "message": "Invalid phone number. Must be 11 digits."}
    if amount <= 0:
        return {"status": "error", "message": "Amount must be greater than zero."}
    if not meter_number or len(str(meter_number)) < 6:
        return {"status": "error", "message": "Invalid meter number"}

    # FIX: Convert disco name to disco_id (API requires integer, not name)
    disco_id = DISCO_ID_MAP.get(disco)
    if not disco_id:
        # Try to use as integer directly if already an ID
        try:
            disco_id = int(disco)
        except (ValueError, TypeError):
            return {"status": "error",
                    "message": f"Unknown electricity provider: {disco}"}

    user = User.query.filter_by(email=user_email).first()
    if not user:
        return {"status": "error", "message": "User not found"}

    selling_price = float(amount)

    discount, applied_coupon, coupon_error = coupon_service.validate_and_price(
        user, coupon_code, "electricity", selling_price)
    if coupon_error:
        return {"status": "error", "message": coupon_error}
    charge_amount = round(selling_price - (discount or 0), 2)

    if user.wallet_balance < charge_amount:
        return {"status": "error",
                "message": f"Insufficient balance. Available: ₦{user.wallet_balance:,.2f}"}

    reference = _make_reference("ELEC", user.id)
    transaction = Transaction(
        user_id=user.id, reference=reference, type="electricity",
        service_type="electricity", amount=charge_amount, profit=0.0,
        status="pending",
        details={"disco": disco, "disco_id": disco_id,
                 "meter_number": meter_number,
                 "meter_type": meter_type, "phone": phone,
                 "selling_price": selling_price, "coupon_code": applied_coupon.code if applied_coupon else None,
                 "discount_amount": discount or 0}
    )
    db.session.add(transaction)
    db.session.commit()

    # FIX: correct field names from CheapDataHub API docs
    # disco_id (int), meter_number, amount, meter_type, phone
    payload = {
        "disco_id":     disco_id,       # FIX: was "disco" (name), now disco_id (integer)
        "meter_number": meter_number,
        "meter_type":   meter_type,     # "prepaid" or "postpaid"
        "amount":       selling_price,
        "phone":        phone,          # FIX: was "phone_number", now "phone"
    }
    api_result = cheapdatahub_request("electricity/purchase/", data=payload)

    if _is_success(api_result):
        profit_percent = PROFIT_MARGINS["electricity"]
        cost_price     = selling_price / (1 + profit_percent / 100)
        profit_amount  = round(charge_amount - cost_price, 2)

        # FIX: token is nested inside data.token not top-level token
        token = (api_result.get("token")
                 or api_result.get("data", {}).get("token", ""))

        user.wallet_balance = round(user.wallet_balance - charge_amount, 2)
        transaction.status = "success"
        transaction.profit = profit_amount
        transaction.details.update({
            "cost_price":    round(cost_price, 2),
            "token":         token,
            "units":         api_result.get("data", {}).get("units", ""),
            "api_reference": api_result.get("reference") or api_result.get("transaction_id"),
        })
        db.session.add(Profit(
            transaction_id=transaction.id, user_id=user.id,
            category="electricity", amount=profit_amount
        ))
        _award_referral_commission(user, charge_amount)
        if applied_coupon:
            coupon_service.redeem_coupon(applied_coupon, user, transaction, discount, category="electricity")
        record_purchase(transaction)
        award_cashback(transaction)
        db.session.commit()
        return {
            "status":  "success",
            "message": "Electricity purchase successful",
            "data": {
                "reference":     reference,
                "token":         token,
                "units":         api_result.get("data", {}).get("units", ""),
                "profit_amount": profit_amount,
                "selling_price": selling_price,
                "discount_amount": discount or 0,
                "amount_charged": charge_amount,
                "new_balance":   round(user.wallet_balance, 2),
            }
        }
    else:
        error_msg = api_result.get("message", "Electricity purchase failed")
        transaction.status = "failed"
        transaction.details["error"] = error_msg
        db.session.commit()
        return {"status": "error", "message": error_msg}


# ── CABLE TV ───────────────────────────────────────────────────────────────────
def buy_cable_tv(plan_id, smartcard, user_email, phone="", coupon_code=None):
    if not smartcard or len(str(smartcard)) < 6:
        return {"status": "error", "message": "Invalid smartcard/IUC number"}

    plan = CablePlan.query.filter_by(plan_id=plan_id).first()
    if not plan:
        return {"status": "error", "message": "Invalid cable plan ID"}

    user = User.query.filter_by(email=user_email).first()
    if not user:
        return {"status": "error", "message": "User not found"}

    selling_price = float(plan.selling_price)

    discount, applied_coupon, coupon_error = coupon_service.validate_and_price(
        user, coupon_code, "cable_tv", selling_price)
    if coupon_error:
        return {"status": "error", "message": coupon_error}
    charge_amount = round(selling_price - (discount or 0), 2)

    if user.wallet_balance < charge_amount:
        return {"status": "error",
                "message": f"Insufficient balance. Available: ₦{user.wallet_balance:,.2f}"}

    reference = _make_reference("CABLE", user.id)
    transaction = Transaction(
        user_id=user.id, reference=reference, type="cable_tv",
        service_type="cable_tv", amount=charge_amount, profit=0.0,
        status="pending",
        details={"plan_id": plan_id, "smartcard": smartcard,
                 "provider": plan.provider, "plan_name": plan.plan_name,
                 "selling_price": selling_price, "coupon_code": applied_coupon.code if applied_coupon else None,
                 "discount_amount": discount or 0}
    )
    db.session.add(transaction)
    db.session.commit()

    # FIX: correct field names from CheapDataHub API docs
    # plan_id, cardnumber, phone
    payload = {
        "plan_id":    plan_id,      # FIX: was "package_id"
        "cardnumber": smartcard,    # FIX: was "smartcard_number"
        "phone":      phone or user.phone,  # FIX: added phone (required)
    }
    api_result = cheapdatahub_request("cable/purchase/", data=payload)

    if _is_success(api_result):
        profit_amount = round(charge_amount - float(plan.cost_price), 2)
        user.wallet_balance = round(user.wallet_balance - charge_amount, 2)
        transaction.status = "success"
        transaction.profit = profit_amount
        transaction.details.update({
            "cost_price":    float(plan.cost_price),
            "api_reference": api_result.get("reference") or api_result.get("transaction_id"),
        })
        db.session.add(Profit(
            transaction_id=transaction.id, user_id=user.id,
            category="cable_tv", amount=profit_amount
        ))
        _award_referral_commission(user, charge_amount)
        if applied_coupon:
            coupon_service.redeem_coupon(applied_coupon, user, transaction, discount, category="cable_tv")
        record_purchase(transaction)
        award_cashback(transaction)
        db.session.commit()
        return {
            "status":  "success",
            "message": "Cable TV subscription successful",
            "data": {
                "reference":     reference,
                "api_reference": api_result.get("reference"),
                "provider":      plan.provider,
                "plan_name":     plan.plan_name,
                "profit_amount": profit_amount,
                "selling_price": selling_price,
                "discount_amount": discount or 0,
                "amount_charged": charge_amount,
                "new_balance":   round(user.wallet_balance, 2),
            }
        }
    else:
        error_msg = api_result.get("message", "Cable TV subscription failed")
        transaction.status = "failed"
        transaction.details["error"] = error_msg
        db.session.commit()
        return {"status": "error", "message": error_msg}
