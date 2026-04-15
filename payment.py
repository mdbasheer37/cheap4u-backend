import hmac
import hashlib
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


def create_paystack_customer(email, first_name, last_name, phone):
    """Create a Paystack customer and return customer_code."""
    secret_key = current_app.config['PAYSTACK_SECRET_KEY']
    url = "https://api.paystack.co/customer"
    headers = {
        "Authorization": f"Bearer {secret_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "email": email,
        "first_name": first_name,
        "last_name": last_name,
        "phone": phone
    }
    response = requests.post(url, json=payload, headers=headers, timeout=30)
    data = response.json()
    if data.get('status'):
        return data['data']['customer_code']
    else:
        raise Exception(f"Paystack customer creation failed: {data.get('message')}")

def create_dedicated_virtual_account(customer_code):
    """Create a dedicated virtual account for a customer."""
    secret_key = current_app.config['PAYSTACK_SECRET_KEY']
    url = "https://api.paystack.co/dedicated_account"
    headers = {
        "Authorization": f"Bearer {secret_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "customer": customer_code,
        "preferred_bank": "wema-bank"  # You can change to "test-bank" for sandbox
    }
    response = requests.post(url, json=payload, headers=headers, timeout=30)
    data = response.json()
    if data.get('status'):
        account = data['data']
        return {
            'account_number': account['account_number'],
            'bank_name': account['bank']['name'],
            'account_name': account['account_name']
        }
    else:
        raise Exception(f"DVA creation failed: {data.get('message')}")

# payment.py - Add route

@payment_bp.route('/account-details', methods=['GET'])
@jwt_required()
def get_account_details():
    """Return the user's virtual account details."""
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

    return jsonify({
        'status': 'success',
        'data': {
            'account_number': user.virtual_account_number,
            'bank_name': user.virtual_bank_name,
            'account_name': user.virtual_account_name
        }
    })


@payment_bp.route('/webhook', methods=['POST'])
def paystack_webhook():
    """Handle Paystack webhook events."""
    # Verify signature
    secret_key = current_app.config['PAYSTACK_SECRET_KEY']
    signature = request.headers.get('x-paystack-signature')
    if not signature:
        return jsonify({'status': 'error', 'message': 'Missing signature'}), 401

    body = request.get_data()
    expected_signature = hmac.new(
        secret_key.encode('utf-8'),
        body,
        hashlib.sha512
    ).hexdigest()

    if not hmac.compare_digest(signature, expected_signature):
        return jsonify({'status': 'error', 'message': 'Invalid signature'}), 401

    data = request.get_json()
    event = data.get('event')

    if event == 'charge.success':
        charge_data = data['data']
        reference = charge_data['reference']
        amount = charge_data['amount'] / 100  # Convert from kobo
        customer_code = charge_data['customer']['customer_code']
        status = charge_data['status']

        # Find user by paystack_customer_code
        user = User.query.filter_by(paystack_customer_code=customer_code).first()
        if not user:
            return jsonify({'status': 'error', 'message': 'User not found'}), 404

        # Check for duplicate transaction
        existing = Transaction.query.filter_by(reference=reference).first()
        if existing:
            return jsonify({'status': 'success', 'message': 'Already processed'}), 200

        # Credit wallet and record transaction
        user.wallet_balance += amount
        transaction = Transaction(
            user_id=user.id,
            reference=reference,
            type='wallet_funding',
            service_type='bank_transfer',
            amount=amount,
            status='success',
            details={'source': 'virtual_account', 'customer_code': customer_code}
        )
        db.session.add(transaction)
        db.session.commit()

        return jsonify({'status': 'success', 'message': 'Wallet credited'}), 200

    # Handle other events if needed
    return jsonify({'status': 'success', 'message': 'Event ignored'}), 200


# payment.py (paystack_webhook modification)

@payment_bp.route('/webhook/paystack', methods=['POST'])
def paystack_webhook():
    data = request.get_json()
    if data.get('event') == 'charge.success':
        reference = data['data']['reference']
        transaction = Transaction.query.filter_by(reference=reference).first()
        if transaction and transaction.status != 'success':
            transaction.status = 'success'
            if transaction.type == 'wallet_funding' and transaction.user_id:
                user = User.query.get(transaction.user_id)
                if user:
                    # Check if this is the user's FIRST successful funding
                    first_funding = not Transaction.query.filter(
                        Transaction.user_id == user.id,
                        Transaction.type == 'wallet_funding',
                        Transaction.status == 'success'
                    ).first()  # this query will return the current transaction as well? Need careful check.

                    # Better: check if user.wallet_balance was 0 before? Or add a flag 'has_received_referral_bonus' on User.
                    # We'll add a flag to User model: referral_bonus_claimed (boolean, default False)
                    
                    if not user.referral_bonus_claimed and user.referred_by_user_id:
                        # Grant signup bonus
                        referrer = User.query.get(user.referred_by_user_id)
                        if referrer:
                            bonus_amount = 10.0  # configurable
                            referrer.referral_earnings += bonus_amount
                            user.referral_bonus_claimed = True
                            
                            # Log the bonus transaction
                            ref_tx = ReferralTransaction(
                                referrer_id=referrer.id,
                                referred_user_id=user.id,
                                amount=bonus_amount,
                                type='signup_bonus'
                            )
                            db.session.add(ref_tx)
                    
                    user.wallet_balance += transaction.amount
            db.session.commit()
    return jsonify({'status': 'success'}), 200 
