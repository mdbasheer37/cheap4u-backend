# airtime_to_cash.py
# Integration with automation.airtimetocash.com's "Airtime to Cash" API.
#
# How this actually works (reverse of a normal VTU purchase):
#   1. User picks a network + enters THEIR OWN phone number + an amount of
#      airtime they want to convert to cash.
#   2. We ask AirtimeToCash to send an OTP to that phone (proves the user
#      really controls that SIM).
#   3. User enters the OTP -> we verify it -> AirtimeToCash gives us a
#      sessionId for that phone/network.
#   4. We check quota availability for the requested amount.
#   5. User enters their SIM's own airtime Transfer PIN (this is THEIR
#      phone's transfer PIN, e.g. the PIN behind their network's airtime-
#      transfer USSD code - NOT a Cheap4U PIN) -> we call Transfer Airtime.
#   6. On success, AirtimeToCash has drained that airtime from the user's
#      SIM. We then credit the user's CHEAP4U WALLET with 98% of the
#      amountConverted (2% is Cheap4U's profit), recorded as a normal
#      Transaction + Profit row, same as any other service.
#
# The Bearer token below must stay a backend secret (env var) - never send
# it to the frontend. All 4 endpoints below require the user to already be
# logged into Cheap4U (JWT), so random people can't hammer your
# AirtimeToCash quota for free.

import os
import re
import uuid
import logging
import requests
from datetime import datetime
from models import db, User, Transaction, Profit

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AIRTIMETOCASH_API_TOKEN = os.environ.get("AIRTIMETOCASH_API_TOKEN", "")
AIRTIMETOCASH_BASE_URL = os.environ.get(
    "AIRTIMETOCASH_BASE_URL", "https://automation.airtimetocash.com"
)

# Cheap4U's own profit margin on Airtime-to-Cash conversions.
# User is credited (100 - this)% of the amount AirtimeToCash confirms
# converted. Default 2% per your instruction ("profit 2%").
PROFIT_MARGIN_AIRTIME_TO_CASH = float(
    os.environ.get("PROFIT_MARGIN_AIRTIME_TO_CASH", "2")
)

# Per-network amount limits, straight from AirtimeToCash's docs.
NETWORK_LIMITS = {
    "MTN":     (50, 10000),
    "AIRTEL":  (50, 20000),
    "GLO":     (50, 1000),
    "9MOBILE": (50, 20000),
}


def _headers(with_auth=True):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if with_auth:
        headers["Authorization"] = f"Bearer {AIRTIMETOCASH_API_TOKEN}"
    return headers


def _request(endpoint, data, with_auth=True, timeout=30):
    """Wrapper for AirtimeToCash API calls - mirrors cheapdatahub_request()."""
    url = f"{AIRTIMETOCASH_BASE_URL}{endpoint}"
    logger.info(f"AirtimeToCash Request: POST {url} | Data: { {k: v for k, v in data.items() if k not in ('pin',)} }")
    try:
        response = requests.post(url, json=data, headers=_headers(with_auth), timeout=timeout)
        try:
            result = response.json()
        except Exception:
            logger.error(f"AirtimeToCash non-JSON response HTTP {response.status_code}: {response.text[:200]}")
            return {"code": 3000, "message": f"Provider error (HTTP {response.status_code})"}
        logger.info(f"AirtimeToCash Response: {result}")
        return result
    except requests.exceptions.Timeout:
        return {"code": 3000, "message": "Request timed out"}
    except requests.exceptions.ConnectionError:
        return {"code": 3000, "message": "Network connection error"}
    except Exception as e:
        return {"code": 3000, "message": f"Request error: {str(e)}"}


def _is_success(api_result):
    """AirtimeToCash uses numeric JSON 'code' values, not status strings.
    2000 = success. Everything else in their docs is a failure/pending state."""
    return api_result.get("code") == 2000


def _make_reference(user_id):
    """10-40 char unique reference, as required by the Transfer Airtime endpoint."""
    return f"A2C{uuid.uuid4().hex[:12].upper()}{user_id}"


def _validate_network(network):
    network = (network or "").strip().upper()
    if network not in NETWORK_LIMITS:
        return None, {"status": "error", "message": f"Unsupported network: {network}"}
    return network, None


def _validate_phone(phone):
    phone = (phone or "").strip()
    if not phone or len(phone) != 11 or not phone.isdigit():
        return None, {"status": "error", "message": "Invalid phone number (must be 11 digits)"}
    return phone, None


def _to_intl(phone):
    """AirtimeToCash's docs show numbers like '0806******73' - local format
    with leading 0 is what their examples use, so we pass phone through as-is."""
    return phone


def generate_otp(network, phone):
    network, err = _validate_network(network)
    if err:
        return err
    phone, err = _validate_phone(phone)
    if err:
        return err

    payload = {"networkName": network, "sender": _to_intl(phone)}
    result = _request("/api/v1/generate/otp", payload, with_auth=False)

    if _is_success(result):
        return {"status": "success", "message": result.get("message", "OTP sent successfully")}
    return {"status": "error", "message": result.get("message", "Failed to send OTP")}


def verify_otp(network, phone, otp):
    network, err = _validate_network(network)
    if err:
        return err
    phone, err = _validate_phone(phone)
    if err:
        return err
    otp = (otp or "").strip()
    if not otp or not otp.isdigit():
        return {"status": "error", "message": "Invalid OTP"}

    payload = {"networkName": network, "sender": _to_intl(phone), "otp": otp}
    result = _request("/api/v1/verify/otp", payload, with_auth=False)

    if _is_success(result):
        data = result.get("data", {})
        return {
            "status": "success",
            "message": result.get("message", "OTP verified"),
            "data": {
                "session_id": data.get("sessionId"),
                "airtime_balance": data.get("airtimeBalance"),
                "tariff": data.get("tariff"),
                "type": data.get("type"),
            },
        }
    return {"status": "error", "message": result.get("message", "Invalid or expired OTP")}


def check_quota(network, amount):
    network, err = _validate_network(network)
    if err:
        return err
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return {"status": "error", "message": "Invalid amount"}

    min_amt, max_amt = NETWORK_LIMITS[network]
    if amount < min_amt or amount > max_amt:
        return {
            "status": "error",
            "message": f"{network} conversion amount must be between ₦{min_amt:,} and ₦{max_amt:,}",
        }

    payload = {"networkName": network, "amount": int(amount)}
    result = _request("/api/v1/check/quota/availability", payload, with_auth=True)

    # NOTE: per AirtimeToCash's own docs, their *success* example for this
    # endpoint actually returns code 5030 ("Recipient(s) Available") rather
    # than 2000 - handle both as "available".
    if result.get("code") in (2000, 5030):
        return {"status": "success", "message": result.get("message", "Recipient(s) available")}
    return {"status": "error", "message": result.get("message", "No recipients available right now - try again shortly")}


def transfer_airtime(user, network, phone, amount, sim_pin, session_id):
    """
    Executes the actual airtime-to-cash conversion, then credits the user's
    Cheap4U wallet with (100 - PROFIT_MARGIN_AIRTIME_TO_CASH)% of the
    confirmed amountConverted.
    """
    network, err = _validate_network(network)
    if err:
        return err
    phone, err = _validate_phone(phone)
    if err:
        return err
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return {"status": "error", "message": "Invalid amount"}

    min_amt, max_amt = NETWORK_LIMITS[network]
    if amount < min_amt or amount > max_amt:
        return {
            "status": "error",
            "message": f"{network} conversion amount must be between ₦{min_amt:,} and ₦{max_amt:,}",
        }

    sim_pin = (sim_pin or "").strip()
    if not sim_pin:
        return {"status": "error", "message": "SIM transfer PIN is required"}
    if not session_id:
        return {"status": "error", "message": "Missing session - please verify OTP again"}

    reference = _make_reference(user.id)
    transaction = Transaction(
        user_id=user.id, reference=reference, type="airtime_to_cash",
        service_type="airtime_to_cash", amount=amount, profit=0.0,
        status="pending",
        details={"network": network, "phone": phone, "airtime_amount": amount}
    )
    db.session.add(transaction)
    db.session.commit()

    payload = {
        "networkName": network,
        "sender": _to_intl(phone),
        "amount": int(amount),
        "reference": reference,
        "pin": sim_pin,
        "sessionId": session_id,
    }
    result = _request("/api/v1/transfer/airtime", payload, with_auth=True)

    if _is_success(result):
        data = result.get("data", {})
        # amountConverted comes back like "₦50" - strip non-numeric chars
        raw_converted = str(data.get("amountConverted", amount))
        converted_amount = float(re.sub(r"[^\d.]", "", raw_converted) or amount)

        credited_amount = round(converted_amount * (1 - PROFIT_MARGIN_AIRTIME_TO_CASH / 100), 2)
        profit_amount = round(converted_amount - credited_amount, 2)

        user.wallet_balance = round(user.wallet_balance + credited_amount, 2)
        transaction.status = "success"
        transaction.amount = converted_amount
        transaction.profit = profit_amount
        transaction.details.update({
            "credited_amount": credited_amount,
            "provider_message": result.get("message"),
            "recipient": data.get("recipient"),
            "automation_charges": data.get("automationCharges"),
            "api_session_id": data.get("sessionId"),
        })
        db.session.add(Profit(
            transaction_id=transaction.id, user_id=user.id,
            category="airtime_to_cash", amount=profit_amount
        ))
        db.session.commit()

        return {
            "status": "success",
            "message": "Airtime converted successfully!",
            "data": {
                "reference": reference,
                "converted_amount": converted_amount,
                "credited_amount": credited_amount,
                "new_balance": round(user.wallet_balance, 2),
            },
        }

    # Failure / pending - map AirtimeToCash's documented codes to a clear message
    code = result.get("code")
    transaction.status = "failed" if code != 4000 else "pending"
    transaction.details.update({"provider_message": result.get("message"), "provider_code": code})
    db.session.commit()

    message = result.get("message", "Airtime conversion failed")
    if code == 4010:
        message = "Session expired - please verify your phone number again"
    elif code == 4290:
        message = "Too many requests - please try again in a moment"
    elif code == 5030:
        message = "No recipients available right now - try again shortly"
    elif code == 4000:
        message = "Conversion is pending confirmation - check your history shortly"

    return {"status": "error", "message": message, "reference": reference}
