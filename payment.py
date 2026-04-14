from flask import Blueprint, request, jsonify, current_app
from datetime import datetime
import requests
from flask import current_app 
import json
from models import db, User, Transaction, Profit
from flask_jwt_extended import jwt_required, get_jwt_identity
import paystackapi
from paystackapi.transaction import Transaction as PaystackTransaction

payment_bp = Blueprint('payment', __name__)

@payment_bp.route('/initialize', methods=['POST'])
def initialize_payment():
    """Initialize Paystack payment"""
    data = request.get_json()
    
    email = data.get('email')
    amount = data.get('amount')
    service_type = data.get('service_type', 'wallet_funding')
    service_details = data.get('service_details', {})
    callback_url = data.get('callback_url', f"{current_app.config['BACKEND_URL']}/api/payment/webhook/paystack")
    
    if not email or not amount:
        return jsonify({'status': 'error', 'message': 'Email and amount required'}), 400
    
    # Initialize Paystack transaction
    paystack_secret = current_app.config['PAYSTACK_SECRET_KEY']
    
    headers = {
        'Authorization': f'Bearer {paystack_secret}',
        'Content-Type': 'application/json'
    }
    
    payload = {
        'email': email,
        'amount': int(amount * 100),  # Convert to kobo
        'callback_url': callback_url,
        'metadata': {
            'service_type': service_type,
            'service_details': service_details
        }
    }
    
    try:
        response = requests.post(
            'https://api.paystack.co/transaction/initialize',
            json=payload,
            headers=headers,
            timeout=30
        )
        
        result = response.json()
        
        if result.get('status'):
            # Create transaction record
            reference = result['data']['reference']
            
            # Get user ID if exists
            user = User.query.filter_by(email=email).first()
            user_id = user.id if user else None
            
            transaction = Transaction(
                user_id=user_id,
                reference=reference,
                type='wallet_funding',
                service_type=service_type,
                amount=amount,
                status='pending',
                details=service_details
            )
            db.session.add(transaction)
            db.session.commit()
            
            return jsonify({
                'status': 'success',
                'data': {
                    'authorization_url': result['data']['authorization_url'],
                    'reference': reference
                }
            })
        else:
            return jsonify({'status': 'error', 'message': result.get('message', 'Payment initialization failed')}), 400
            
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@payment_bp.route('/verify/<reference>', methods=['GET'])
def verify_payment(reference):
    """Verify Paystack payment"""
    paystack_secret = current_app.config['PAYSTACK_SECRET_KEY']
    
    headers = {
        'Authorization': f'Bearer {paystack_secret}',
        'Content-Type': 'application/json'
    }
    
    try:
        response = requests.get(
            f'https://api.paystack.co/transaction/verify/{reference}',
            headers=headers,
            timeout=30
        )
        
        result = response.json()
        
        if result.get('status'):
            transaction = Transaction.query.filter_by(reference=reference).first()
            
            if not transaction:
                return jsonify({'status': 'error', 'message': 'Transaction not found'}), 404
            
            # Update transaction status
            if result['data']['status'] == 'success':
                transaction.status = 'success'
                
                # Credit user wallet if it's wallet funding
                if transaction.type == 'wallet_funding' and transaction.user_id:
                    user = User.query.get(transaction.user_id)
                    if user:
                        user.wallet_balance += transaction.amount
                        db.session.commit()
                
                db.session.commit()
                
                return jsonify({
                    'status': 'success',
                    'data': {
                        'status': 'success',
                        'amount': transaction.amount,
                        'reference': reference
                    }
                })
            else:
                transaction.status = 'failed'
                db.session.commit()
                return jsonify({
                    'status': 'success',
                    'data': {
                        'status': result['data']['status'],
                        'message': 'Payment not completed'
                    }
                })
        else:
            return jsonify({'status': 'error', 'message': result.get('message', 'Verification failed')}), 400
            
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@payment_bp.route('/virtual-account', methods=['POST'])
def create_virtual_account():
    """Create virtual account for bank transfer (simplified)"""
    data = request.get_json()
    
    email = data.get('email')
    amount = data.get('amount')
    
    # In production, you would use Paystack's dynamic account API
    # For now, return placeholder data
    
    return jsonify({
        'status': 'success',
        'data': {
            'bank': {'name': 'Test Bank'},
            'account_number': '0123456789',
            'account_name': 'Test Account',
            'reference': f'REF_{datetime.utcnow().timestamp()}',
            'amount': amount
        }
    })

@payment_bp.route('/webhook/paystack', methods=['POST'])
def paystack_webhook():
    """Handle Paystack webhook"""
    data = request.get_json()
    
    if data.get('event') == 'charge.success':
        reference = data['data']['reference']
        
        # Verify and process transaction
        transaction = Transaction.query.filter_by(reference=reference).first()
        
        if transaction and transaction.status != 'success':
            transaction.status = 'success'
            
            # Credit user wallet if applicable
            if transaction.type == 'wallet_funding' and transaction.user_id:
                user = User.query.get(transaction.user_id)
                if user:
                    user.wallet_balance += transaction.amount
                    db.session.commit()
            
            db.session.commit()
    
    return jsonify({'status': 'success'}), 200 
