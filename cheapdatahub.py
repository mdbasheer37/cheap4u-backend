import os
import requests
from flask import current_app, jsonify
from models import db, User, Transaction, Profit

# Configuration for CheapDataHub
CHEAPDATAHUB_API_KEY = os.environ.get("CHEAPDATAHUB_API_KEY", "")
CHEAPDATAHUB_BASE_URL = os.environ.get("CHEAPDATAHUB_BASE_URL", "https://api.cheapdatahub.com/v1")

# Profit margins per service (in percentage)
PROFIT_MARGINS = {
    "airtime": float(os.environ.get("PROFIT_MARGIN_AIRTIME", "5")),
    "data": float(os.environ.get("PROFIT_MARGIN_DATA", "10")),
    "electricity": float(os.environ.get("PROFIT_MARGIN_ELECTRICITY", "5")),
    "cable_tv": float(os.environ.get("PROFIT_MARGIN_CABLE_TV", "5")),
    "exam_pin": float(os.environ.get("PROFIT_MARGIN_EXAM_PIN", "10")),
}

def cheapdatahub_request(endpoint, method="POST", data=None):
    """Make a request to CheapDataHub API."""
    headers = {
        "Authorization": f"Bearer {CHEAPDATAHUB_API_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{CHEAPDATAHUB_BASE_URL}/{endpoint}"
    try:
        if method.upper() == "GET":
            response = requests.get(url, headers=headers, timeout=30)
        else:
            response = requests.post(url, json=data, headers=headers, timeout=30)
        return response.json()
    except Exception as e:
        return {"status": "error", "message": str(e)}

def calculate_profit(cost_price, profit_margin_percent):
    """Calculate selling price based on cost price and profit margin."""
    profit_amount = cost_price * (profit_margin_percent / 100)
    selling_price = cost_price + profit_amount
    return selling_price, profit_amount

def buy_airtime(network, phone, amount, user_email):
    """Purchase airtime via CheapDataHub."""
    # 1. Get cost price from CheapDataHub
    cost_data = cheapdatahub_request(
        f"airtime/cost?network={network}&amount={amount}", method="GET"
    )
    if cost_data.get("status") != "success":
        return {"status": "error", "message": "Failed to get airtime cost"}

    cost_price = cost_data["data"]["cost_price"]
    # 2. Calculate selling price and profit
    selling_price, profit_amount = calculate_profit(cost_price, PROFIT_MARGINS["airtime"])
    # 3. Purchase from CheapDataHub
    purchase_data = {
        "network": network,
        "phone": phone,
        "amount": amount,
        "selling_price": selling_price,
    }
    result = cheapdatahub_request("airtime/purchase", method="POST", data=purchase_data)
    if result.get("status") == "success":
        # Record transaction
        user = User.query.filter_by(email=user_email).first()
        transaction = Transaction(
            user_id=user.id if user else None,
            reference=result.get("reference"),
            type="airtime",
            service_type="airtime",
            amount=selling_price,
            profit=profit_amount,
            status="success",
            details={
                "network": network,
                "phone": phone,
                "amount": amount,
                "cost_price": cost_price,
                "profit_margin": PROFIT_MARGINS["airtime"],
            },
        )
        db.session.add(transaction)
        if user:
            profit = Profit(
                transaction_id=transaction.id,
                user_id=user.id,
                category="airtime",
                amount=profit_amount,
            )
            db.session.add(profit)
            if user.wallet_balance >= selling_price:
                user.wallet_balance -= selling_price
        db.session.commit()
        return {
            "status": "success",
            "message": "Airtime purchase successful",
            "data": {
                "transaction_id": result.get("transaction_id"),
                "profit_amount": profit_amount,
                "selling_price": selling_price,
            },
        }
    else:
        return {
            "status": "error",
            "message": result.get("message", "Airtime purchase failed"),
        }

def buy_data(network, phone, plan_code, user_email):
    """Purchase data via CheapDataHub."""
    # 1. Get cost price from CheapDataHub
    cost_data = cheapdatahub_request(
        f"data/cost?network={network}&plan_code={plan_code}", method="GET"
    )
    if cost_data.get("status") != "success":
        return {"status": "error", "message": "Failed to get data cost"}

    cost_price = cost_data["data"]["cost_price"]
    # 2. Calculate selling price and profit
    selling_price, profit_amount = calculate_profit(cost_price, PROFIT_MARGINS["data"])
    # 3. Purchase from CheapDataHub
    purchase_data = {
        "network": network,
        "phone": phone,
        "plan_code": plan_code,
        "selling_price": selling_price,
    }
    result = cheapdatahub_request("data/purchase", method="POST", data=purchase_data)
    if result.get("status") == "success":
        # Record transaction
        user = User.query.filter_by(email=user_email).first()
        transaction = Transaction(
            user_id=user.id if user else None,
            reference=result.get("reference"),
            type="data",
            service_type="data",
            amount=selling_price,
            profit=profit_amount,
            status="success",
            details={
                "network": network,
                "phone": phone,
                "plan_code": plan_code,
                "cost_price": cost_price,
                "profit_margin": PROFIT_MARGINS["data"],
            },
        )
        db.session.add(transaction)
        if user:
            profit = Profit(
                transaction_id=transaction.id,
                user_id=user.id,
                category="data",
                amount=profit_amount,
            )
            db.session.add(profit)
            if user.wallet_balance >= selling_price:
                user.wallet_balance -= selling_price
        db.session.commit()
        return {
            "status": "success",
            "message": "Data purchase successful",
            "data": {
                "transaction_id": result.get("transaction_id"),
                "profit_amount": profit_amount,
                "selling_price": selling_price,
            },
        }
    else:
        return {
            "status": "error",
            "message": result.get("message", "Data purchase failed"),
        }

def buy_electricity(disco, meter_number, meter_type, amount, phone, user_email):
    """Purchase electricity via CheapDataHub."""
    # 1. Get cost price from CheapDataHub
    cost_data = cheapdatahub_request(
        f"electricity/cost?disco={disco}&meter_number={meter_number}&meter_type={meter_type}&amount={amount}",
        method="GET",
    )
    if cost_data.get("status") != "success":
        return {"status": "error", "message": "Failed to get electricity cost"}

    cost_price = cost_data["data"]["cost_price"]
    # 2. Calculate selling price and profit
    selling_price, profit_amount = calculate_profit(cost_price, PROFIT_MARGINS["electricity"])
    # 3. Purchase from CheapDataHub
    purchase_data = {
        "disco": disco,
        "meter_number": meter_number,
        "meter_type": meter_type,
        "amount": amount,
        "phone": phone,
        "selling_price": selling_price,
    }
    result = cheapdatahub_request("electricity/purchase", method="POST", data=purchase_data)
    if result.get("status") == "success":
        # Record transaction
        user = User.query.filter_by(email=user_email).first()
        transaction = Transaction(
            user_id=user.id if user else None,
            reference=result.get("reference"),
            type="electricity",
            service_type="electricity",
            amount=selling_price,
            profit=profit_amount,
            status="success",
            details={
                "disco": disco,
                "meter_number": meter_number,
                "meter_type": meter_type,
                "phone": phone,
                "token": result.get("token", ""),
                "cost_price": cost_price,
                "profit_margin": PROFIT_MARGINS["electricity"],
            },
        )
        db.session.add(transaction)
        if user:
            profit = Profit(
                transaction_id=transaction.id,
                user_id=user.id,
                category="electricity",
                amount=profit_amount,
            )
            db.session.add(profit)
            if user.wallet_balance >= selling_price:
                user.wallet_balance -= selling_price
        db.session.commit()
        return {
            "status": "success",
            "message": "Electricity purchase successful",
            "data": {
                "transaction_id": result.get("transaction_id"),
                "token": result.get("token", ""),
                "profit_amount": profit_amount,
                "selling_price": selling_price,
            },
        }
    else:
        return {
            "status": "error",
            "message": result.get("message", "Electricity purchase failed"),
        }

def buy_cable_tv(provider, package, smartcard, amount, user_email):
    """Purchase cable TV subscription via CheapDataHub."""
    # 1. Get cost price from CheapDataHub
    cost_data = cheapdatahub_request(
        f"cable/cost?provider={provider}&package={package}", method="GET"
    )
    if cost_data.get("status") != "success":
        return {"status": "error", "message": "Failed to get cable TV cost"}

    cost_price = cost_data["data"]["cost_price"]
    # 2. Calculate selling price and profit
    selling_price, profit_amount = calculate_profit(cost_price, PROFIT_MARGINS["cable_tv"])
    # 3. Purchase from CheapDataHub
    purchase_data = {
        "provider": provider,
        "package": package,
        "smartcard": smartcard,
        "amount": amount,
        "selling_price": selling_price,
    }
    result = cheapdatahub_request("cable/purchase", method="POST", data=purchase_data)
    if result.get("status") == "success":
        # Record transaction
        user = User.query.filter_by(email=user_email).first()
        transaction = Transaction(
            user_id=user.id if user else None,
            reference=result.get("reference"),
            type="cable_tv",
            service_type="cable_tv",
            amount=selling_price,
            profit=profit_amount,
            status="success",
            details={
                "provider": provider,
                "package": package,
                "smartcard": smartcard,
                "cost_price": cost_price,
                "profit_margin": PROFIT_MARGINS["cable_tv"],
            },
        )
        db.session.add(transaction)
        if user:
            profit = Profit(
                transaction_id=transaction.id,
                user_id=user.id,
                category="cable_tv",
                amount=profit_amount,
            )
            db.session.add(profit)
            if user.wallet_balance >= selling_price:
                user.wallet_balance -= selling_price
        db.session.commit()
        return {
            "status": "success",
            "message": "Cable TV subscription successful",
            "data": {
                "transaction_id": result.get("transaction_id"),
                "profit_amount": profit_amount,
                "selling_price": selling_price,
            },
        }
    else:
        return {
            "status": "error",
            "message": result.get("message", "Cable TV subscription failed"),
        }

def buy_exam_pin(exam_type, quantity, user_email):
    """Purchase exam pins via CheapDataHub."""
    # 1. Get cost price from CheapDataHub
    cost_data = cheapdatahub_request(
        f"exam/cost?exam_type={exam_type}&quantity={quantity}", method="GET"
    )
    if cost_data.get("status") != "success":
        return {"status": "error", "message": "Failed to get exam pin cost"}

    cost_price = cost_data["data"]["cost_price"]
    # 2. Calculate selling price and profit
    selling_price, profit_amount = calculate_profit(cost_price, PROFIT_MARGINS["exam_pin"])
    # 3. Purchase from CheapDataHub
    purchase_data = {
        "exam_type": exam_type,
        "quantity": quantity,
        "selling_price": selling_price,
    }
    result = cheapdatahub_request("exam/purchase", method="POST", data=purchase_data)
    if result.get("status") == "success":
        # Record transaction
        user = User.query.filter_by(email=user_email).first()
        transaction = Transaction(
            user_id=user.id if user else None,
            reference=result.get("reference"),
            type="exam_pin",
            service_type="exam_pin",
            amount=selling_price,
            profit=profit_amount,
            status="success",
            details={
                "exam_type": exam_type,
                "quantity": quantity,
                "pins": result.get("pins", []),
                "cost_price": cost_price,
                "profit_margin": PROFIT_MARGINS["exam_pin"],
            },
        )
        db.session.add(transaction)
        if user:
            profit = Profit(
                transaction_id=transaction.id,
                user_id=user.id,
                category="exam_pin",
                amount=profit_amount,
            )
            db.session.add(profit)
            if user.wallet_balance >= selling_price:
                user.wallet_balance -= selling_price
        db.session.commit()
        return {
            "status": "success",
            "message": "Exam PIN purchase successful",
            "data": {
                "transaction_id": result.get("transaction_id"),
                "pins": result.get("pins", []),
                "profit_amount": profit_amount,
                "selling_price": selling_price,
            },
        }
    else:
        return {
            "status": "error",
            "message": result.get("message", "Exam PIN purchase failed"),
        } 
