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
import time
import requests
import logging
from datetime import datetime
from models import db, User, Transaction, Profit, DataPlan, CablePlan, ReferralTransaction
from challenge import record_purchase
from cashback import award_cashback
import gamification as gamification_service
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
# NOTE: the frontend's disco-selection screen sends the plain display name
# (e.g. "Abuja Electric", not "Abuja Electric AEDC") as the exact string
# stored in self.selected_electricity_provider — this dict does an exact
# (case-sensitive) match, so every name the frontend can actually send has
# to be a key here. "Abuja Electric" and "Jos Electric" were missing
# entirely (only the longer official names were), so choosing either of
# those two discos failed outright with "Unknown electricity provider".
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
    "Abuja Electric":          5,   # FIX: matches the name the frontend actually sends
    "AEDC":                    5,
    "Kaduna Electric":         6,
    "KEDCO":                   6,
    "Port Harcourt Electric":  7,
    "PHED":                    7,
    "Jos Electricity JEDplc":  8,
    "Jos Electric":            8,   # FIX: matches the name the frontend actually sends
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
        return {"status": "false", "message": "Request timed out", "_transport_error": True}
    except requests.exceptions.ConnectionError:
        return {"status": "false", "message": "Network connection error", "_transport_error": True}
    except Exception as e:
        return {"status": "false", "message": f"Request error: {str(e)}", "_transport_error": True}


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
    _t0 = time.monotonic()
    api_result = cheapdatahub_request("airtime/purchase/", data=payload)
    processing_time_ms = round((time.monotonic() - _t0) * 1000)

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
            "processing_time_ms": processing_time_ms,
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
        gamification_service.record_activity(transaction)
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
        transaction.details["processing_time_ms"] = processing_time_ms
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
    _t0 = time.monotonic()
    api_result = cheapdatahub_request("data/purchase/", data=payload)
    processing_time_ms = round((time.monotonic() - _t0) * 1000)

    if _is_success(api_result):
        profit_amount = round(charge_amount - float(plan.cost_price), 2)
        user.wallet_balance = round(user.wallet_balance - charge_amount, 2)
        transaction.status = "success"
        transaction.profit = profit_amount
        transaction.details.update({
            "cost_price":    float(plan.cost_price),
            "api_reference": api_result.get("reference") or api_result.get("transaction_id"),
        })
        transaction.details["processing_time_ms"] = processing_time_ms
        db.session.add(Profit(
            transaction_id=transaction.id, user_id=user.id,
            category="data", amount=profit_amount
        ))
        _award_referral_commission(user, charge_amount)
        if applied_coupon:
            coupon_service.redeem_coupon(applied_coupon, user, transaction, discount, category="data")
        record_purchase(transaction)
        award_cashback(transaction)
        gamification_service.record_activity(transaction)
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
        transaction.details["processing_time_ms"] = processing_time_ms
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
        # Fall back to a case/whitespace-insensitive match before giving up —
        # this is what actually failed for "Abuja Electric"/"Jos Electric"
        # above; keeping this fallback means a future naming tweak on either
        # side (frontend display name vs this map) degrades gracefully
        # instead of hard-failing the purchase again.
        normalized = disco.strip().lower()
        for name, id_ in DISCO_ID_MAP.items():
            if name.strip().lower() == normalized:
                disco_id = id_
                break
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
        gamification_service.record_activity(transaction)
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
        gamification_service.record_activity(transaction)
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


# ── EXAM PINS (WAEC / NECO / NABTEB / JAMB) ───────────────────────────────────
#
# Official CheapDataHub flow (https://www.cheapdatahub.ng/api_documentation/):
#   GET  exam-pin/products/   -> active products + your reseller price
#   POST exam-pin/purchase/   -> {"product_id": <int>, "quantity": 1 | 2 | 5}
#     reply: {"status": "true", "data": {"transaction": {"amount": 3400},
#             "delivery": {"exam_name": "WAEC", "quantity": 1,
#                          "pins": ["<pin><=><serial>", ...]}}}
#
# Product IDs and prices come from the provider, so they are looked up live
# (and cached briefly) instead of being hard-coded. The docs don't show the
# exact shape of the products reply, so the parser below is deliberately
# tolerant; GET /api/admin/exam-pin/products shows the raw reply and which
# product was picked for each exam. To pin a product explicitly, set
#   CHEAPDATAHUB_EXAM_PRODUCT_IDS="WAEC=1,NECO=2,NABTEB=3,JAMB=4"

import math

PROFIT_MARGIN_EXAM_PIN = float(os.environ.get("PROFIT_MARGIN_EXAM_PIN", "10"))
EXAM_TYPES = ("WAEC", "NECO", "NABTEB", "JAMB")
EXAM_ALLOWED_QUANTITIES = (1, 2, 5)           # the only quantities the provider accepts
_EXAM_CACHE_SECONDS = 120
_exam_cache = {"at": 0.0, "products": None}


def _first_list_of_dicts(obj, depth=0):
    """Find the first list of dicts anywhere in an API reply (the products)."""
    if depth > 4:
        return None
    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj):
            return obj
        for x in obj:
            found = _first_list_of_dicts(x, depth + 1)
            if found:
                return found
    elif isinstance(obj, dict):
        for key in ("data", "products", "results", "items", "exam_pins", "exam_pin_products"):
            if key in obj:
                found = _first_list_of_dicts(obj[key], depth + 1)
                if found:
                    return found
        for value in obj.values():
            found = _first_list_of_dicts(value, depth + 1)
            if found:
                return found
    return None


def _pick(d, keys):
    for k in keys:
        if k in d and d[k] is not None and d[k] != "":
            return d[k]
    return None


def _to_float(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = "".join(ch for ch in str(value) if ch.isdigit() or ch == ".")
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _exam_overrides():
    """CHEAPDATAHUB_EXAM_PRODUCT_IDS='WAEC=1,NECO=2' -> {'WAEC': 1, 'NECO': 2}."""
    out = {}
    for part in os.environ.get("CHEAPDATAHUB_EXAM_PRODUCT_IDS", "").split(","):
        if "=" in part:
            name, _, pid = part.partition("=")
            try:
                out[name.strip().upper()] = int(pid.strip())
            except ValueError:
                pass
    return out


def normalize_exam_products(api_result, overrides=None):
    """
    Turn the provider's products reply into {exam: {product_id, name, unit_cost}}.
    unit_cost is what CheapDataHub charges YOUR wallet per PIN.
    """
    overrides = overrides or {}
    items = _first_list_of_dicts(api_result) or []
    candidates = {}            # exam -> [(score, product)]
    for it in items:
        try:
            pid = int(_pick(it, ("id", "product_id", "pk")))
        except (TypeError, ValueError):
            continue
        active = _pick(it, ("is_active", "active", "enabled", "available"))
        if active is not None and str(active).strip().lower() in ("false", "0", "no", "none"):
            continue
        name_fields = [str(it[k]) for k in ("name", "title", "product_name", "exam_name", "exam", "type", "label")
                       if k in it and it[k] is not None]
        name_text = " ".join(name_fields).upper()
        all_text = " ".join(str(v) for v in it.values() if isinstance(v, str)).upper()
        exam = next((e for e in EXAM_TYPES if e in name_text), None) or \
               next((e for e in EXAM_TYPES if e in all_text), None)
        if not exam:
            continue
        unit_cost = _to_float(_pick(it, ("price", "amount", "reseller_price", "selling_price",
                                         "unit_price", "cost", "rate")))
        if not unit_cost or unit_cost <= 0:
            continue
        # prefer the plain "result checker" PIN over registration/GCE/verification variants
        score = 2 if ("RESULT" in name_text or "CHECKER" in name_text) else \
                (0 if any(w in name_text for w in ("REGISTRATION", "GCE", "VERIFICATION")) else 1)
        product = {"product_id": pid, "name": (name_fields[0] if name_fields else exam),
                   "unit_cost": unit_cost}
        candidates.setdefault(exam, []).append((score, product))

    out = {}
    for exam, options in candidates.items():
        forced = overrides.get(exam)
        if forced is not None:
            match = next((p for _, p in options if p["product_id"] == forced), None)
            if match:
                out[exam] = match
                continue
        out[exam] = max(options, key=lambda sp: sp[0])[1]
    return out


def get_exam_products(force=False):
    """(products_by_exam, error_message). Cached for a couple of minutes."""
    now = time.time()
    if not force and _exam_cache["products"] is not None and now - _exam_cache["at"] < _EXAM_CACHE_SECONDS:
        return _exam_cache["products"], None
    result = cheapdatahub_request("exam-pin/products/", method="GET", timeout=20)
    if not _is_success(result) and not _first_list_of_dicts(result):
        return None, result.get("message") or "Couldn't load exam PIN prices"
    products = normalize_exam_products(result, _exam_overrides())
    if not products:
        return None, "No exam PIN products are available right now"
    _exam_cache["products"], _exam_cache["at"] = products, now
    return products, None


def exam_unit_selling_price(unit_cost):
    """Price per PIN charged to the user: provider cost + your margin, rounded up to the naira."""
    return int(math.ceil(round(unit_cost * (1 + PROFIT_MARGIN_EXAM_PIN / 100.0), 2)))


def get_exam_prices():
    """What the app shows on the exam screen."""
    products, error = get_exam_products()
    if error:
        return None, error
    prices = {e: exam_unit_selling_price(p["unit_cost"]) for e, p in products.items()}
    return {
        "prices": prices,
        "available": {e: (e in prices) for e in EXAM_TYPES},
        "quantities": list(EXAM_ALLOWED_QUANTITIES),
    }, None


def parse_exam_pins(delivery):
    """['<pin><=><serial>', ...] -> [{'pin': ..., 'serial': ...}, ...]."""
    out = []
    raw_pins = delivery if isinstance(delivery, list) else ((delivery or {}).get("pins", []) or [])
    for raw in raw_pins:
        text = str(raw).strip()
        if not text:
            continue
        if "<=>" in text:
            a, _, b = text.partition("<=>")
            a, b = a.strip(), b.strip()
            # the serial number is the one that starts with letters (e.g. WRN...)
            if a.isdigit() or not b:
                pin, serial = a, (b or None)
            elif b.isdigit():
                pin, serial = b, a
            else:
                pin, serial = a, b
        else:
            pin, serial = text, None
        out.append({"pin": pin, "serial": serial})
    return out


def buy_exam_pin(exam_name, quantity, user_email, selling_price=None, coupon_code=None):
    """
    Buy WAEC / NECO / NABTEB / JAMB PINs through CheapDataHub.
    quantity must be 1, 2 or 5 (provider rule). The price is ALWAYS worked out
    here from the provider's live price; selling_price (what the app showed) is
    only used to make sure the user isn't charged something different.
    """
    exam_name = str(exam_name or "").upper().strip()
    if exam_name not in EXAM_TYPES:
        return {"status": "error", "message": "Unsupported exam type. Use WAEC, NECO, NABTEB or JAMB"}
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        return {"status": "error", "message": "Invalid quantity"}
    if quantity not in EXAM_ALLOWED_QUANTITIES:
        return {"status": "error", "message": "You can buy 1, 2 or 5 PINs at a time"}

    products, error = get_exam_products(force=True)
    if error:
        return {"status": "error", "message": f"{error}. Please try again shortly."}
    product = products.get(exam_name)
    if not product:
        return {"status": "error", "message": f"{exam_name} PINs are not available right now"}

    unit_sell = exam_unit_selling_price(product["unit_cost"])
    selling_total = float(unit_sell * quantity)
    if selling_price is not None:
        try:
            shown = float(selling_price)
        except (TypeError, ValueError):
            return {"status": "error", "message": "Invalid selling price"}
        if abs(shown - selling_total) > 1:
            return {"status": "error", "code": "price_changed",
                    "message": f"The price has changed to ₦{selling_total:,.0f}. "
                               f"Please reopen the exam screen to see the new price.",
                    "data": {"price": selling_total}}

    user = User.query.filter_by(email=user_email).with_for_update().first()
    if not user:
        return {"status": "error", "message": "User not found"}

    discount, applied_coupon, coupon_error = coupon_service.validate_and_price(
        user, coupon_code, "exam_pin", selling_total)
    if coupon_error:
        return {"status": "error", "message": coupon_error}
    charge_amount = round(selling_total - (discount or 0), 2)

    if user.wallet_balance < charge_amount:
        return {"status": "error", "message": f"Insufficient balance. Available: ₦{user.wallet_balance:,.2f}"}

    reference = _make_reference("EXAM", user.id)
    transaction = Transaction(
        user_id=user.id, reference=reference, type="exam_pin", service_type="exam_pin",
        amount=charge_amount, profit=0.0, status="pending",
        details={"exam_name": exam_name, "product_id": product["product_id"], "quantity": quantity,
                 "selling_price": selling_total,
                 "coupon_code": applied_coupon.code if applied_coupon else None,
                 "discount_amount": discount or 0, "provider": "cheapdatahub"},
    )
    db.session.add(transaction)
    db.session.commit()

    api_result = cheapdatahub_request(
        "exam-pin/purchase/", data={"product_id": product["product_id"], "quantity": quantity}, timeout=45)

    if _is_success(api_result):
        data = api_result.get("data") or {}
        pins = parse_exam_pins(data.get("delivery"))
        if not pins:
            # Provider charged us but we couldn't read the PINs: never lose them silently.
            transaction.status = "pending"
            transaction.details.update({"error": "PINs not readable in provider reply",
                                        "provider_reply": api_result})
            db.session.commit()
            logger.error(f"[ExamPIN] {reference} succeeded at provider but no pins parsed: {api_result}")
            return {"status": "error",
                    "message": "Your purchase went through but the PINs couldn't be displayed. "
                               "Please contact support with reference " + reference}

        cost_price = _to_float((data.get("transaction") or {}).get("amount")) or round(product["unit_cost"] * quantity, 2)
        profit_amount = round(charge_amount - cost_price, 2)

        user.wallet_balance -= charge_amount
        transaction.status = "success"
        transaction.profit = profit_amount
        transaction.details.update({
            "cost_price": round(cost_price, 2),
            "pins": pins,
            "pin": pins[0]["pin"], "serial": pins[0]["serial"],
        })
        db.session.add(Profit(transaction_id=transaction.id, user_id=user.id,
                              category="exam_pin", amount=profit_amount))
        if applied_coupon:
            coupon_service.redeem_coupon(applied_coupon, user, transaction, discount, category="exam_pin")
        record_purchase(transaction)
        award_cashback(transaction)
        gamification_service.record_activity(transaction)
        db.session.commit()

        logger.info(f"[ExamPIN] SUCCESS {reference} {exam_name} x{quantity} | cost=₦{cost_price} profit=₦{profit_amount}")
        return {
            "status": "success",
            "message": f"{exam_name} PIN purchase successful",
            "data": {
                "reference":       reference,
                "exam_name":       exam_name,
                "quantity":        quantity,
                "pins":            pins,
                "pin":             pins[0]["pin"],          # first PIN (older app versions)
                "serial":          pins[0]["serial"],
                "profit_amount":   profit_amount,
                "selling_price":   selling_total,
                "discount_amount": discount or 0,
                "amount_charged":  charge_amount,
                "new_balance":     round(user.wallet_balance, 2),
            },
        }

    # ── not successful ─────────────────────────────────────────────────
    provider_msg = api_result.get("message") or "Exam PIN purchase failed"
    if api_result.get("_transport_error"):
        # No answer at all: the PIN may or may not have been bought. Keep it
        # PENDING (not failed) so it can be checked against CheapDataHub's
        # transactions list, and don't charge the user.
        transaction.status = "pending"
        transaction.details["error"] = provider_msg
        db.session.commit()
        logger.error(f"[ExamPIN] NO ANSWER {reference}: {provider_msg}")
        return {"status": "error",
                "message": "We couldn't confirm your purchase. You have not been charged. "
                           "If you need help, contact support with reference " + reference}

    transaction.status = "failed"
    transaction.details["error"] = provider_msg
    db.session.commit()
    logger.error(f"[ExamPIN] FAILED {reference} | {provider_msg}")
    lowered = provider_msg.lower()
    if "insufficient" in lowered or "balance" in lowered:
        # that's OUR wallet at the provider - don't expose it to the customer
        provider_msg = "This service is temporarily unavailable. Please try again later."
    return {"status": "error", "message": provider_msg}
