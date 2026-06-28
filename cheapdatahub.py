# cheapdatahub.py — Production-ready CheapDataHub provider integration
# Changes from previous version:
#   1. try/except with db.session.rollback() around every DB commit
#   2. with_for_update() on user query — prevents race conditions
#   3. Wallet deducted AFTER provider confirms success (not before)
#   4. All error paths guaranteed to rollback wallet if not yet deducted
#   5. Provider IDs confirmed: MTN=1, Glo=2, Airtel=3, 9Mobile=4

import os
import uuid
import requests
import logging
from datetime import datetime
from models import db, User, Transaction, Profit, DataPlan, CablePlan, ReferralTransaction

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CHEAPDATAHUB_API_KEY  = os.environ.get("CHEAPDATAHUB_API_KEY", "")
CHEAPDATAHUB_BASE_URL = os.environ.get(
    "CHEAPDATAHUB_BASE_URL",
    "https://www.cheapdatahub.ng/api/v1/resellers/"
)
PROFIT_MARGINS = {
    "airtime":     float(os.environ.get("PROFIT_MARGIN_AIRTIME", "5")),
    "electricity": float(os.environ.get("PROFIT_MARGIN_ELECTRICITY", "5")),
}

# Electricity disco name → CheapDataHub ID
DISCO_ID_MAP = {
    "Ikeja Electric":          1,  "IKEDC":  1,
    "Eko Electric":            2,  "EKEDC":  2,
    "Ibadan Electric":         3,  "IBEDC":  3,
    "Enugu Electric":          4,  "EEDC":   4,
    "Abuja Electric AEDC":     5,  "AEDC":   5,
    "Kaduna Electric":         6,  "KEDCO":  6,
    "Port Harcourt Electric":  7,  "PHED":   7,
    "Jos Electricity JEDplc":  8,  "JED":    8,
    "Kano Electric":           9,  "KEDCO Kano": 9,
    "Benin Electric":          10, "BEDC":   10,
    # Also accept numeric strings
    "1": 1, "2": 2, "3": 3, "4": 4, "5": 5,
    "6": 6, "7": 7, "8": 8, "9": 9, "10": 10,
}


# ── API wrapper ────────────────────────────────────────────────────────────────

def cheapdatahub_request(endpoint, method="POST", data=None, timeout=30):
    """Wrapper for all CheapDataHub API calls."""
    headers = {
        "Authorization": f"Bearer {CHEAPDATAHUB_API_KEY}",
        "Content-Type":  "application/json",
    }
    url = f"{CHEAPDATAHUB_BASE_URL.rstrip('/')}/{endpoint.lstrip('/')}"
    logger.info(f"[CheapDataHub] {method} {url} | payload={data}")
    try:
        if method.upper() == "GET":
            resp = requests.get(url, headers=headers, timeout=timeout)
        else:
            resp = requests.post(url, json=data, headers=headers, timeout=timeout)
        try:
            result = resp.json()
        except Exception:
            logger.error(f"[CheapDataHub] Non-JSON HTTP {resp.status_code}: {resp.text[:300]}")
            return {"status": "false", "message": f"Provider error (HTTP {resp.status_code})"}
        logger.info(f"[CheapDataHub] Response: {result}")
        return result
    except requests.exceptions.Timeout:
        logger.error("[CheapDataHub] Timeout")
        return {"status": "false", "message": "Request timed out. Please try again."}
    except requests.exceptions.ConnectionError:
        logger.error("[CheapDataHub] Connection error")
        return {"status": "false", "message": "Network error. Please try again."}
    except Exception as e:
        logger.error(f"[CheapDataHub] Exception: {e}")
        return {"status": "false", "message": f"Request error: {str(e)}"}


def _is_success(api_result):
    """Handle 'true', 'True', 'success', 'successful' from CheapDataHub."""
    status = str(api_result.get("status", "")).lower()
    return status in ("true", "success", "successful")


def _make_reference(prefix, user_id):
    """UUID-based reference — no timestamp collisions."""
    return f"{prefix}_{uuid.uuid4().hex[:8].upper()}_{user_id}"


def _award_referral_commission(user, selling_price):
    """Give 2% referral commission to the user who referred this user."""
    if not user or not getattr(user, 'referred_by_user_id', None):
        return
    try:
        commission = round(selling_price * 0.02, 2)
        referrer = User.query.get(user.referred_by_user_id)
        if referrer:
            referrer.referral_earnings += commission
            db.session.add(ReferralTransaction(
                referrer_id=referrer.id,
                referred_user_id=user.id,
                amount=commission,
                type='commission'
            ))
    except Exception as e:
        logger.error(f"[Referral] Commission error: {e}")


# ── AIRTIME ────────────────────────────────────────────────────────────────────

def buy_airtime(network, phone, amount, user_email):
    """Purchase airtime via CheapDataHub."""
    # Input validation
    phone = str(phone or "").strip()
    if not phone or len(phone) != 11 or not phone.isdigit():
        return {"status": "error", "message": "Invalid phone number. Must be 11 digits."}
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return {"status": "error", "message": "Invalid amount."}
    if amount <= 0:
        return {"status": "error", "message": "Amount must be greater than zero."}

    provider_map = {"MTN": 1, "Glo": 2, "Airtel": 3, "9Mobile": 4}
    provider_id  = provider_map.get(network)
    if not provider_id:
        return {"status": "error", "message": f"Unsupported network: {network}"}

    # Fetch user with row lock (prevents race conditions)
    user = User.query.filter_by(email=user_email).with_for_update().first()
    if not user:
        return {"status": "error", "message": "User not found."}

    selling_price = round(float(amount), 2)
    if user.wallet_balance < selling_price:
        return {"status": "error",
                "message": f"Insufficient balance. Available: ₦{user.wallet_balance:,.2f}"}

    # Create pending transaction BEFORE calling provider
    reference = _make_reference("AIRTIME", user.id)
    transaction = Transaction(
        user_id=user.id, reference=reference,
        type="airtime", service_type="airtime",
        amount=selling_price, profit=0.0, status="pending",
        details={"network": network, "phone": phone, "amount": selling_price}
    )
    try:
        db.session.add(transaction)
        db.session.flush()  # get transaction.id without committing
    except Exception as e:
        db.session.rollback()
        logger.error(f"[Airtime] DB error creating transaction: {e}")
        return {"status": "error", "message": "Database error. Please try again."}

    # Call provider
    payload    = {"provider_id": provider_id, "phone_number": phone, "amount": selling_price}
    api_result = cheapdatahub_request("airtime/purchase/", data=payload)

    if _is_success(api_result):
        try:
            profit_percent = PROFIT_MARGINS["airtime"]
            cost_price     = round(selling_price / (1 + profit_percent / 100), 2)
            profit_amount  = round(selling_price - cost_price, 2)

            user.wallet_balance = round(user.wallet_balance - selling_price, 2)
            transaction.status  = "success"
            transaction.profit  = profit_amount
            transaction.details.update({
                "cost_price":    cost_price,
                "api_reference": api_result.get("reference") or api_result.get("transaction_id"),
            })
            db.session.add(Profit(
                transaction_id=transaction.id, user_id=user.id,
                category="airtime", amount=profit_amount
            ))
            _award_referral_commission(user, selling_price)
            db.session.commit()

            logger.info(f"[Airtime] SUCCESS {reference} ₦{selling_price} → {network} {phone}")
            return {
                "status":  "success",
                "message": "Airtime purchase successful",
                "data": {
                    "reference":     reference,
                    "api_reference": api_result.get("reference"),
                    "network":       network,
                    "phone":         phone,
                    "amount":        selling_price,
                    "profit_amount": profit_amount,
                    "new_balance":   round(user.wallet_balance, 2),
                }
            }
        except Exception as e:
            db.session.rollback()
            logger.error(f"[Airtime] DB commit error after success: {e}", exc_info=True)
            return {"status": "error", "message": "Transaction succeeded but database update failed. Contact support."}
    else:
        try:
            error_msg = api_result.get("message", "Airtime purchase failed")
            transaction.status = "failed"
            transaction.details["error"] = error_msg
            db.session.commit()
        except Exception:
            db.session.rollback()
        logger.warning(f"[Airtime] FAILED {reference}: {error_msg}")
        return {"status": "error", "message": error_msg}


# ── DATA ───────────────────────────────────────────────────────────────────────

def buy_data(plan_id, phone, user_email):
    """Purchase data bundle via CheapDataHub."""
    phone = str(phone or "").strip()
    if not phone or len(phone) != 11 or not phone.isdigit():
        return {"status": "error", "message": "Invalid phone number. Must be 11 digits."}

    plan = DataPlan.query.filter_by(plan_id=plan_id).first()
    if not plan:
        return {"status": "error", "message": f"Invalid plan ID: {plan_id}"}

    user = User.query.filter_by(email=user_email).with_for_update().first()
    if not user:
        return {"status": "error", "message": "User not found."}

    selling_price = round(float(plan.selling_price), 2)
    if user.wallet_balance < selling_price:
        return {"status": "error",
                "message": f"Insufficient balance. Available: ₦{user.wallet_balance:,.2f}"}

    reference = _make_reference("DATA", user.id)
    transaction = Transaction(
        user_id=user.id, reference=reference,
        type="data", service_type="data",
        amount=selling_price, profit=0.0, status="pending",
        details={"plan_id": plan_id, "phone": phone,
                 "plan_name": plan.size, "provider": plan.provider}
    )
    try:
        db.session.add(transaction)
        db.session.flush()
    except Exception as e:
        db.session.rollback()
        logger.error(f"[Data] DB error creating transaction: {e}")
        return {"status": "error", "message": "Database error. Please try again."}

    payload    = {"bundle_id": plan_id, "phone_number": phone}
    api_result = cheapdatahub_request("data/purchase/", data=payload)

    if _is_success(api_result):
        try:
            profit_amount = round(selling_price - float(plan.cost_price), 2)
            user.wallet_balance = round(user.wallet_balance - selling_price, 2)
            transaction.status  = "success"
            transaction.profit  = profit_amount
            transaction.details.update({
                "cost_price":    float(plan.cost_price),
                "api_reference": api_result.get("reference") or api_result.get("transaction_id"),
            })
            db.session.add(Profit(
                transaction_id=transaction.id, user_id=user.id,
                category="data", amount=profit_amount
            ))
            _award_referral_commission(user, selling_price)
            db.session.commit()

            logger.info(f"[Data] SUCCESS {reference} plan={plan_id} → {phone}")
            return {
                "status":  "success",
                "message": "Data purchase successful",
                "data": {
                    "reference":     reference,
                    "api_reference": api_result.get("reference"),
                    "plan_name":     plan.size,
                    "provider":      plan.provider,
                    "phone":         phone,
                    "profit_amount": profit_amount,
                    "selling_price": selling_price,
                    "new_balance":   round(user.wallet_balance, 2),
                }
            }
        except Exception as e:
            db.session.rollback()
            logger.error(f"[Data] DB commit error after success: {e}", exc_info=True)
            return {"status": "error", "message": "Transaction succeeded but database update failed. Contact support."}
    else:
        try:
            error_msg = api_result.get("message", "Data purchase failed")
            transaction.status = "failed"
            transaction.details["error"] = error_msg
            db.session.commit()
        except Exception:
            db.session.rollback()
        logger.warning(f"[Data] FAILED {reference}: {error_msg}")
        return {"status": "error", "message": error_msg}


# ── ELECTRICITY ────────────────────────────────────────────────────────────────

def buy_electricity(disco, meter_number, meter_type, amount, phone, user_email):
    """Purchase electricity token via CheapDataHub."""
    phone = str(phone or "").strip()
    if not phone or len(phone) != 11 or not phone.isdigit():
        return {"status": "error", "message": "Invalid phone number. Must be 11 digits."}
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return {"status": "error", "message": "Invalid amount."}
    if amount <= 0:
        return {"status": "error", "message": "Amount must be greater than zero."}

    meter_number = str(meter_number or "").strip()
    if not meter_number or len(meter_number) < 6:
        return {"status": "error", "message": "Invalid meter number."}

    # Resolve disco to integer ID
    disco_id = DISCO_ID_MAP.get(str(disco))
    if not disco_id:
        try:
            disco_id = int(disco)
            if disco_id < 1 or disco_id > 10:
                raise ValueError
        except (ValueError, TypeError):
            return {"status": "error", "message": f"Unknown electricity provider: {disco}"}

    user = User.query.filter_by(email=user_email).with_for_update().first()
    if not user:
        return {"status": "error", "message": "User not found."}

    selling_price = round(float(amount), 2)
    if user.wallet_balance < selling_price:
        return {"status": "error",
                "message": f"Insufficient balance. Available: ₦{user.wallet_balance:,.2f}"}

    reference = _make_reference("ELEC", user.id)
    transaction = Transaction(
        user_id=user.id, reference=reference,
        type="electricity", service_type="electricity",
        amount=selling_price, profit=0.0, status="pending",
        details={"disco": disco, "disco_id": disco_id,
                 "meter_number": meter_number,
                 "meter_type": meter_type, "phone": phone}
    )
    try:
        db.session.add(transaction)
        db.session.flush()
    except Exception as e:
        db.session.rollback()
        logger.error(f"[Electricity] DB error creating transaction: {e}")
        return {"status": "error", "message": "Database error. Please try again."}

    payload = {
        "disco_id":     disco_id,
        "meter_number": meter_number,
        "meter_type":   meter_type,
        "amount":       selling_price,
        "phone":        phone,
    }
    api_result = cheapdatahub_request("electricity/purchase/", data=payload)

    if _is_success(api_result):
        try:
            profit_percent = PROFIT_MARGINS["electricity"]
            cost_price     = round(selling_price / (1 + profit_percent / 100), 2)
            profit_amount  = round(selling_price - cost_price, 2)
            token = (api_result.get("token")
                     or api_result.get("data", {}).get("token", ""))
            units = api_result.get("data", {}).get("units", "")

            user.wallet_balance = round(user.wallet_balance - selling_price, 2)
            transaction.status  = "success"
            transaction.profit  = profit_amount
            transaction.details.update({
                "cost_price":    cost_price,
                "token":         token,
                "units":         units,
                "api_reference": api_result.get("reference") or api_result.get("transaction_id"),
            })
            db.session.add(Profit(
                transaction_id=transaction.id, user_id=user.id,
                category="electricity", amount=profit_amount
            ))
            _award_referral_commission(user, selling_price)
            db.session.commit()

            logger.info(f"[Electricity] SUCCESS {reference} ₦{selling_price} disco={disco}")
            return {
                "status":  "success",
                "message": "Electricity purchase successful",
                "data": {
                    "reference":     reference,
                    "token":         token,
                    "units":         units,
                    "disco":         disco,
                    "meter_number":  meter_number,
                    "profit_amount": profit_amount,
                    "selling_price": selling_price,
                    "new_balance":   round(user.wallet_balance, 2),
                }
            }
        except Exception as e:
            db.session.rollback()
            logger.error(f"[Electricity] DB commit error after success: {e}", exc_info=True)
            return {"status": "error", "message": "Transaction succeeded but database update failed. Contact support."}
    else:
        try:
            error_msg = api_result.get("message", "Electricity purchase failed")
            transaction.status = "failed"
            transaction.details["error"] = error_msg
            db.session.commit()
        except Exception:
            db.session.rollback()
        logger.warning(f"[Electricity] FAILED {reference}: {error_msg}")
        return {"status": "error", "message": error_msg}


# ── CABLE TV ───────────────────────────────────────────────────────────────────

def buy_cable_tv(plan_id, smartcard, user_email, phone=""):
    """Purchase cable TV subscription via CheapDataHub."""
    smartcard = str(smartcard or "").strip()
    if not smartcard or len(smartcard) < 6:
        return {"status": "error", "message": "Invalid smartcard/IUC number."}

    plan = CablePlan.query.filter_by(plan_id=plan_id).first()
    if not plan:
        return {"status": "error", "message": f"Invalid cable plan ID: {plan_id}"}

    user = User.query.filter_by(email=user_email).with_for_update().first()
    if not user:
        return {"status": "error", "message": "User not found."}

    selling_price = round(float(plan.selling_price), 2)
    if user.wallet_balance < selling_price:
        return {"status": "error",
                "message": f"Insufficient balance. Available: ₦{user.wallet_balance:,.2f}"}

    phone = str(phone or "").strip() or user.phone or ""

    reference = _make_reference("CABLE", user.id)
    transaction = Transaction(
        user_id=user.id, reference=reference,
        type="cable_tv", service_type="cable_tv",
        amount=selling_price, profit=0.0, status="pending",
        details={"plan_id": plan_id, "smartcard": smartcard,
                 "provider": plan.provider, "plan_name": plan.plan_name}
    )
    try:
        db.session.add(transaction)
        db.session.flush()
    except Exception as e:
        db.session.rollback()
        logger.error(f"[CableTV] DB error creating transaction: {e}")
        return {"status": "error", "message": "Database error. Please try again."}

    payload = {
        "plan_id":    plan_id,
        "cardnumber": smartcard,
        "phone":      phone,
    }
    api_result = cheapdatahub_request("cable/purchase/", data=payload)

    if _is_success(api_result):
        try:
            profit_amount = round(selling_price - float(plan.cost_price), 2)
            user.wallet_balance = round(user.wallet_balance - selling_price, 2)
            transaction.status  = "success"
            transaction.profit  = profit_amount
            transaction.details.update({
                "cost_price":    float(plan.cost_price),
                "api_reference": api_result.get("reference") or api_result.get("transaction_id"),
            })
            db.session.add(Profit(
                transaction_id=transaction.id, user_id=user.id,
                category="cable_tv", amount=profit_amount
            ))
            _award_referral_commission(user, selling_price)
            db.session.commit()

            logger.info(f"[CableTV] SUCCESS {reference} {plan.provider} {plan.plan_name} → {smartcard}")
            return {
                "status":  "success",
                "message": "Cable TV subscription successful",
                "data": {
                    "reference":     reference,
                    "api_reference": api_result.get("reference"),
                    "provider":      plan.provider,
                    "plan_name":     plan.plan_name,
                    "smartcard":     smartcard,
                    "profit_amount": profit_amount,
                    "selling_price": selling_price,
                    "new_balance":   round(user.wallet_balance, 2),
                }
            }
        except Exception as e:
            db.session.rollback()
            logger.error(f"[CableTV] DB commit error after success: {e}", exc_info=True)
            return {"status": "error", "message": "Transaction succeeded but database update failed. Contact support."}
    else:
        try:
            error_msg = api_result.get("message", "Cable TV subscription failed")
            transaction.status = "failed"
            transaction.details["error"] = error_msg
            db.session.commit()
        except Exception:
            db.session.rollback()
        logger.warning(f"[CableTV] FAILED {reference}: {error_msg}")
        return {"status": "error", "message": error_msg}
