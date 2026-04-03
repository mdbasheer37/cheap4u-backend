import os
import requests
from models import db, User, Transaction, Profit, DataPlan, CablePlan, ElectricityProvider

CHEAPDATAHUB_API_KEY = os.environ.get("CHEAPDATAHUB_API_KEY", "")
CHEAPDATAHUB_BASE_URL = os.environ.get("CHEAPDATAHUB_BASE_URL", "https://www.cheapdatahub.ng/api/v1/resellers/")

# Profit margins for services that don't have per-plan costs (airtime, electricity)
# For data & cable, we use the per-plan cost from database
PROFIT_MARGINS = {
    "airtime": float(os.environ.get("PROFIT_MARGIN_AIRTIME", "5")),      # 5% profit on airtime
    "electricity": float(os.environ.get("PROFIT_MARGIN_ELECTRICITY", "5")), # 5% profit on electricity
}

def cheapdatahub_request(endpoint, method="POST", data=None):
    headers = {
        "Authorization": f"Bearer {CHEAPDATAHUB_API_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{CHEAPDATAHUB_BASE_URL}{endpoint}"
    try:
        if method.upper() == "GET":
            response = requests.get(url, headers=headers, timeout=30)
        else:
            response = requests.post(url, json=data, headers=headers, timeout=30)
        return response.json()
    except Exception as e:
        return {"status": "false", "message": str(e)}

# ------------------------------------------------------------------
# Airtime (profit margin from env)
# ------------------------------------------------------------------
def buy_airtime(network, phone, amount, user_email):
    provider_map = {"MTN": 1, "Airtel": 2, "Glo": 3, "9Mobile": 4}
    provider_id = provider_map.get(network)
    if not provider_id:
        return {"status": "error", "message": "Unsupported network"}

    payload = {"provider_id": provider_id, "phone_number": phone, "amount": amount}
    result = cheapdatahub_request("airtime/purchase/", data=payload)

    if result.get("status") == "true":
        # Cost price is not returned; assume cost = amount / (1 + profit_margin)
        profit_percent = PROFIT_MARGINS["airtime"]
        cost_price = amount / (1 + profit_percent / 100)
        selling_price = amount
        profit_amount = selling_price - cost_price

        user = User.query.filter_by(email=user_email).first()
        transaction = Transaction(
            user_id=user.id if user else None,
            reference=result.get("reference", f"CDH_{result.get('transaction_id')}"),
            type="airtime",
            service_type="airtime",
            amount=selling_price,
            profit=profit_amount,
            status="success",
            details={"network": network, "phone": phone, "amount": amount, "cost_price": cost_price}
        )
        db.session.add(transaction)
        if user:
            profit = Profit(transaction_id=transaction.id, user_id=user.id, category="airtime", amount=profit_amount)
            db.session.add(profit)
            if user.wallet_balance >= selling_price:
                user.wallet_balance -= selling_price
        db.session.commit()
        return {"status": "success", "message": "Airtime purchase successful",
                "data": {"transaction_id": result.get("transaction_id"), "profit_amount": profit_amount, "selling_price": selling_price}}
    else:
        return {"status": "error", "message": result.get("message", "Airtime purchase failed")}

# ------------------------------------------------------------------
# Data (uses DataPlan table for selling_price and cost_price)
# ------------------------------------------------------------------
def buy_data(plan_id, phone, user_email):
    plan = DataPlan.query.filter_by(plan_id=plan_id).first()
    if not plan:
        return {"status": "error", "message": "Invalid plan ID"}

    payload = {"bundle_id": plan_id, "phone_number": phone}
    result = cheapdatahub_request("data/purchase/", data=payload)

    if result.get("status") == "true":
        selling_price = plan.selling_price
        cost_price = plan.cost_price
        profit_amount = selling_price - cost_price

        user = User.query.filter_by(email=user_email).first()
        transaction = Transaction(
            user_id=user.id if user else None,
            reference=result.get("reference", f"CDH_{result.get('transaction_id')}"),
            type="data",
            service_type="data",
            amount=selling_price,
            profit=profit_amount,
            status="success",
            details={"plan_id": plan_id, "phone": phone, "cost_price": cost_price}
        )
        db.session.add(transaction)
        if user:
            profit = Profit(transaction_id=transaction.id, user_id=user.id, category="data", amount=profit_amount)
            db.session.add(profit)
            if user.wallet_balance >= selling_price:
                user.wallet_balance -= selling_price
        db.session.commit()
        return {"status": "success", "message": "Data purchase successful",
                "data": {"transaction_id": result.get("transaction_id"), "profit_amount": profit_amount, "selling_price": selling_price}}
    else:
        return {"status": "error", "message": result.get("message", "Data purchase failed")}

# ------------------------------------------------------------------
# Electricity (profit margin from env, no per-plan costs)
# ------------------------------------------------------------------
def buy_electricity(disco, meter_number, meter_type, amount, phone, user_email):
    payload = {
        "disco": disco,
        "meter_number": meter_number,
        "meter_type": meter_type,
        "amount": amount,
        "phone_number": phone
    }
    result = cheapdatahub_request("electricity/purchase/", data=payload)

    if result.get("status") == "true":
        profit_percent = PROFIT_MARGINS["electricity"]
        cost_price = amount / (1 + profit_percent / 100)
        selling_price = amount
        profit_amount = selling_price - cost_price

        user = User.query.filter_by(email=user_email).first()
        transaction = Transaction(
            user_id=user.id if user else None,
            reference=result.get("reference", f"CDH_{result.get('transaction_id')}"),
            type="electricity",
            service_type="electricity",
            amount=selling_price,
            profit=profit_amount,
            status="success",
            details={"disco": disco, "meter_number": meter_number, "meter_type": meter_type, "phone": phone,
                     "token": result.get("token", ""), "cost_price": cost_price}
        )
        db.session.add(transaction)
        if user:
            profit = Profit(transaction_id=transaction.id, user_id=user.id, category="electricity", amount=profit_amount)
            db.session.add(profit)
            if user.wallet_balance >= selling_price:
                user.wallet_balance -= selling_price
        db.session.commit()
        return {"status": "success", "message": "Electricity purchase successful",
                "data": {"transaction_id": result.get("transaction_id"), "token": result.get("token", ""),
                         "profit_amount": profit_amount, "selling_price": selling_price}}
    else:
        return {"status": "error", "message": result.get("message", "Electricity purchase failed")}

# ------------------------------------------------------------------
# Cable TV (uses CablePlan table for selling_price and cost_price)
# ------------------------------------------------------------------
def buy_cable_tv(plan_id, smartcard, user_email):
    plan = CablePlan.query.filter_by(plan_id=plan_id).first()
    if not plan:
        return {"status": "error", "message": "Invalid cable plan ID"}

    payload = {"package_id": plan_id, "smartcard_number": smartcard}
    result = cheapdatahub_request("cable/purchase/", data=payload)

    if result.get("status") == "true":
        selling_price = plan.selling_price
        cost_price = plan.cost_price
        profit_amount = selling_price - cost_price

        user = User.query.filter_by(email=user_email).first()
        transaction = Transaction(
            user_id=user.id if user else None,
            reference=result.get("reference", f"CDH_{result.get('transaction_id')}"),
            type="cable_tv",
            service_type="cable_tv",
            amount=selling_price,
            profit=profit_amount,
            status="success",
            details={"plan_id": plan_id, "smartcard": smartcard, "cost_price": cost_price}
        )
        db.session.add(transaction)
        if user:
            profit = Profit(transaction_id=transaction.id, user_id=user.id, category="cable_tv", amount=profit_amount)
            db.session.add(profit)
            if user.wallet_balance >= selling_price:
                user.wallet_balance -= selling_price
        db.session.commit()
        return {"status": "success", "message": "Cable TV subscription successful",
                "data": {"transaction_id": result.get("transaction_id"), "profit_amount": profit_amount, "selling_price": selling_price}}
    else:
        return {"status": "error", "message": result.get("message", "Cable TV subscription failed")}
