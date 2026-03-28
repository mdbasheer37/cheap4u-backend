from flask import Blueprint, request, jsonify, current_app
import requests
from datetime import datetime
from models import db, User, Transaction, Profit
from flask_jwt_extended import jwt_required, get_jwt_identity
import json

vtpass_bp = Blueprint('vtpass', __name__, url_prefix='/api/vtpass')

def vtpass_request(endpoint, method='POST', data=None):
    """Make request to VTPass API"""
    api_key = current_app.config['VTPASS_API_KEY']
    base_url = current_app.config['VTPASS_BASE_URL']
    
    headers = {
        'api-key': api_key,
        'secret-key': api_key,
        'Content-Type': 'application/json'
    }
    
    url = f"{base_url}/{endpoint}"
    
    try:
        if method.upper() == 'GET':
            response = requests.get(url, headers=headers, timeout=30)
        else:
            response = requests.post(url, json=data, headers=headers, timeout=30)
        
        return response.json()
    except Exception as e:
        return {'code': 'error', 'response_description': str(e)}

@vtpass_bp.route('/airtime', methods=['POST'])
def buy_airtime():
    """Purchase airtime via VTPass"""
    data = request.get_json()
    
    network = data.get('network')
    phone = data.get('phone')
    amount = data.get('amount')
    service_id = data.get('service_id')
    user_email = data.get('user_email')
    
    # Prepare VTPass request
    vtpass_data = {
        'serviceID': service_id,
        'billersCode': phone,
        'amount': amount,
        'variation_code': service_id,
        'phone': phone
    }
    
    result = vtpass_request('pay', 'POST', vtpass_data)
    
    if result.get('code') == '000':
        # Transaction successful
        profit_amount = amount * 0.05  # 5% profit margin
        
        # Record transaction
        user = User.query.filter_by(email=user_email).first() if user_email else None
        
        transaction = Transaction(
            user_id=user.id if user else None,
            reference=result.get('requestId'),
            type='airtime',
            service_type='airtime',
            amount=amount,
            profit=profit_amount,
            status='success',
            details={
                'network': network,
                'phone': phone,
                'transaction_id': result.get('transactionId')
            }
        )
        db.session.add(transaction)
        
        # Record profit
        if user:
            profit = Profit(
                transaction_id=transaction.id,
                user_id=user.id,
                category='airtime',
                amount=profit_amount
            )
            db.session.add(profit)
            
            # Deduct from user wallet
            if user.wallet_balance >= amount:
                user.wallet_balance -= amount
        
        db.session.commit()
        
        return jsonify({
            'status': 'success',
            'message': 'Airtime purchase successful',
            'data': {
                'transaction_id': result.get('transactionId'),
                'profit_amount': profit_amount
            }
        })
    else:
        error_msg = result.get('response_description', 'Transaction failed')
        return jsonify({
            'status': 'error',
            'message': f'Airtime purchase failed: {error_msg}'
        }), 400

@vtpass_bp.route('/data', methods=['POST'])
def buy_data():
    """Purchase data via VTPass"""
    data = request.get_json()
    
    network = data.get('network')
    phone = data.get('phone')
    plan_code = data.get('plan_code')
    base_price = data.get('base_price')
    selling_price = data.get('selling_price')
    service_id = data.get('service_id')
    user_email = data.get('user_email')
    
    # Prepare VTPass request
    vtpass_data = {
        'serviceID': service_id,
        'billersCode': phone,
        'amount': selling_price,
        'variation_code': plan_code,
        'phone': phone
    }
    
    result = vtpass_request('pay', 'POST', vtpass_data)
    
    if result.get('code') == '000':
        profit_amount = selling_price - base_price
        
        # Record transaction
        user = User.query.filter_by(email=user_email).first() if user_email else None
        
        transaction = Transaction(
            user_id=user.id if user else None,
            reference=result.get('requestId'),
            type='data',
            service_type='data',
            amount=selling_price,
            profit=profit_amount,
            status='success',
            details={
                'network': network,
                'phone': phone,
                'plan_code': plan_code,
                'transaction_id': result.get('transactionId')
            }
        )
        db.session.add(transaction)
        
        # Record profit
        if user:
            profit = Profit(
                transaction_id=transaction.id,
                user_id=user.id,
                category='data',
                amount=profit_amount
            )
            db.session.add(profit)
            
            # Deduct from user wallet
            if user.wallet_balance >= selling_price:
                user.wallet_balance -= selling_price
        
        db.session.commit()
        
        return jsonify({
            'status': 'success',
            'message': 'Data purchase successful',
            'data': {
                'transaction_id': result.get('transactionId'),
                'profit_amount': profit_amount
            }
        })
    else:
        error_msg = result.get('response_description', 'Transaction failed')
        return jsonify({
            'status': 'error',
            'message': f'Data purchase failed: {error_msg}'
        }), 400

@vtpass_bp.route('/electricity', methods=['POST'])
def buy_electricity():
    """Purchase electricity via VTPass"""
    data = request.get_json()
    
    disco = data.get('disco')
    meter_number = data.get('meter_number')
    meter_type = data.get('meter_type')
    amount = data.get('amount')
    phone = data.get('phone')
    user_email = data.get('user_email')
    
    # Prepare VTPass request
    vtpass_data = {
        'serviceID': disco,
        'billersCode': meter_number,
        'amount': amount,
        'variation_code': meter_type,
        'phone': phone
    }
    
    result = vtpass_request('pay', 'POST', vtpass_data)
    
    if result.get('code') == '000':
        profit_amount = amount * 0.05  # 5% profit margin
        
        # Record transaction
        user = User.query.filter_by(email=user_email).first() if user_email else None
        
        transaction = Transaction(
            user_id=user.id if user else None,
            reference=result.get('requestId'),
            type='electricity',
            service_type='electricity',
            amount=amount,
            profit=profit_amount,
            status='success',
            details={
                'disco': disco,
                'meter_number': meter_number,
                'meter_type': meter_type,
                'phone': phone,
                'token': result.get('response_description', ''),
                'transaction_id': result.get('transactionId')
            }
        )
        db.session.add(transaction)
        
        # Record profit
        if user:
            profit = Profit(
                transaction_id=transaction.id,
                user_id=user.id,
                category='electricity',
                amount=profit_amount
            )
            db.session.add(profit)
            
            # Deduct from user wallet
            if user.wallet_balance >= amount:
                user.wallet_balance -= amount
        
        db.session.commit()
        
        return jsonify({
            'status': 'success',
            'message': 'Electricity purchase successful',
            'data': {
                'transaction_id': result.get('transactionId'),
                'token': result.get('response_description', ''),
                'profit_amount': profit_amount
            }
        })
    else:
        error_msg = result.get('response_description', 'Transaction failed')
        return jsonify({
            'status': 'error',
            'message': f'Electricity purchase failed: {error_msg}'
        }), 400

@vtpass_bp.route('/cable-tv', methods=['POST'])
def buy_cable_tv():
    """Purchase cable TV subscription via VTPass"""
    data = request.get_json()
    
    provider = data.get('provider')
    package = data.get('package')
    smartcard = data.get('smartcard')
    amount = data.get('amount')
    user_email = data.get('user_email')
    
    # Prepare VTPass request
    vtpass_data = {
        'serviceID': provider,
        'billersCode': smartcard,
        'amount': amount,
        'variation_code': package,
        'phone': smartcard
    }
    
    result = vtpass_request('pay', 'POST', vtpass_data)
    
    if result.get('code') == '000':
        profit_amount = amount * 0.05  # 5% profit margin
        
        # Record transaction
        user = User.query.filter_by(email=user_email).first() if user_email else None
        
        transaction = Transaction(
            user_id=user.id if user else None,
            reference=result.get('requestId'),
            type='cable_tv',
            service_type='cable_tv',
            amount=amount,
            profit=profit_amount,
            status='success',
            details={
                'provider': provider,
                'package': package,
                'smartcard': smartcard,
                'transaction_id': result.get('transactionId')
            }
        )
        db.session.add(transaction)
        
        # Record profit
        if user:
            profit = Profit(
                transaction_id=transaction.id,
                user_id=user.id,
                category='cable_tv',
                amount=profit_amount
            )
            db.session.add(profit)
            
            # Deduct from user wallet
            if user.wallet_balance >= amount:
                user.wallet_balance -= amount
        
        db.session.commit()
        
        return jsonify({
            'status': 'success',
            'message': 'Cable TV subscription successful',
            'data': {
                'transaction_id': result.get('transactionId'),
                'profit_amount': profit_amount
            }
        })
    else:
        error_msg = result.get('response_description', 'Transaction failed')
        return jsonify({
            'status': 'error',
            'message': f'Cable TV subscription failed: {error_msg}'
        }), 400 
