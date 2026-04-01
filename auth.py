from flask import Blueprint, request, jsonify
from datetime import datetime, timedelta
from models import db, User, OTP
from utils import generate_referral_code, generate_otp, send_sms, validate_email, validate_phone
from flask_jwt_extended import create_access_token
from flask_jwt_extended import jwt_required, get_jwt_identity
import bcrypt

auth_bp = Blueprint('auth', __name__)



@auth_bp.route('/set-pin', methods=['POST'])
@jwt_required()
def set_transaction_pin():
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

    data = request.get_json()
    old_pin = data.get('old_pin')
    new_pin = data.get('new_pin')

    # If old_pin is provided, verify it (for changing)
    if old_pin:
        if not user.transaction_pin_hash:
            return jsonify({'status': 'error', 'message': 'No PIN set yet'}), 400
        if not bcrypt.checkpw(old_pin.encode('utf-8'), user.transaction_pin_hash.encode('utf-8')):
            return jsonify({'status': 'error', 'message': 'Incorrect current PIN'}), 401

    # Validate new PIN
    if not new_pin or not new_pin.isdigit() or len(new_pin) < 4 or len(new_pin) > 6:
        return jsonify({'status': 'error', 'message': 'PIN must be 4-6 digits'}), 400

    # Hash and save
    pin_hash = bcrypt.hashpw(new_pin.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    user.transaction_pin_hash = pin_hash
    db.session.commit()

    return jsonify({'status': 'success', 'message': 'Transaction PIN set successfully'})

@auth_bp.route('/verify-pin', methods=['POST'])
@jwt_required()
def verify_transaction_pin():
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

    data = request.get_json()
    pin = data.get('pin')
    if not pin:
        return jsonify({'status': 'error', 'message': 'PIN required'}), 400

    if not user.transaction_pin_hash:
        return jsonify({'status': 'error', 'message': 'No PIN set. Please set a transaction PIN first.'}), 400

    if bcrypt.checkpw(pin.encode('utf-8'), user.transaction_pin_hash.encode('utf-8')):
        return jsonify({'status': 'success', 'message': 'PIN verified'})
    else:
        return jsonify({'status': 'error', 'message': 'Incorrect PIN'}), 401

@auth_bp.route('/register', methods=['POST'])
def register():
    """Register a new user"""
    data = request.get_json()
    
    # Validate required fields
    required = ['name', 'email', 'phone', 'password']
    if not all(field in data for field in required):
        return jsonify({'status': 'error', 'message': 'Missing required fields'}), 400
    
    name = data['name']
    email = data['email'].lower()
    phone = data['phone']
    password = data['password']
    referral_code = data.get('referral_code', '')
    
    # Validate email
    if not validate_email(email):
        return jsonify({'status': 'error', 'message': 'Invalid email format'}), 400
    
    # Validate phone
    if not validate_phone(phone):
        return jsonify({'status': 'error', 'message': 'Phone number must be 11 digits'}), 400
    
    # Check if user exists
    if User.query.filter_by(email=email).first():
        return jsonify({'status': 'error', 'message': 'Email already registered'}), 400
    
    if User.query.filter_by(phone=phone).first():
        return jsonify({'status': 'error', 'message': 'Phone number already registered'}), 400
    
    # Create user
    user = User(
        name=name,
        email=email,
        phone=phone,
        referral_code=generate_referral_code()
    )
    user.set_password(password)
    
    # Handle referral
    if referral_code:
        referrer = User.query.filter_by(referral_code=referral_code).first()
        if referrer:
            user.referred_by = referral_code
    
    db.session.add(user)
    db.session.commit()
    
    # Generate and send OTP
    otp_code = generate_otp()
    expires_at = datetime.utcnow() + timedelta(minutes=10)
    
    otp = OTP(
        user_id=user.id,
        email=email,
        phone=phone,
        code=otp_code,
        purpose='registration',
        expires_at=expires_at
    )
    db.session.add(otp)
    db.session.commit()
    
    # Send OTP via SMS
    message = f"Your Cheap4u verification code is {otp_code}. It expires in 10 minutes."
    send_sms(phone, message)
    
    return jsonify({
        'status': 'success',
        'message': 'User registered. OTP sent to your phone.',
        'data': {
            'user_id': user.id,
            'email': email,
            'phone': phone,
            'mock_otp': otp_code if not current_app.config['TERMII_API_KEY'] else None
        }
    })

@auth_bp.route('/verify-otp', methods=['POST'])
def verify_otp():
    """Verify OTP code"""
    data = request.get_json()
    
    if not data.get('user_id') or not data.get('otp_code'):
        return jsonify({'status': 'error', 'message': 'User ID and OTP code required'}), 400
    
    user_id = data['user_id']
    otp_code = data['otp_code']
    
    otp = OTP.query.filter_by(
        user_id=user_id,
        code=otp_code,
        is_used=False
    ).first()
    
    if not otp:
        return jsonify({'status': 'error', 'message': 'Invalid OTP code'}), 400
    
    if datetime.utcnow() > otp.expires_at:
        return jsonify({'status': 'error', 'message': 'OTP has expired'}), 400
    
    # Mark OTP as used
    otp.is_used = True
    
    # Verify user
    user = User.query.get(user_id)
    user.is_verified = True
    
    db.session.commit()
    
    # Generate JWT token
    access_token = create_access_token(identity=user.id)
    
    return jsonify({
        'status': 'success',
        'message': 'Account verified successfully',
        'data': {
            'user': user.to_dict(),
            'session_token': access_token
        }
    })

@auth_bp.route('/resend-otp', methods=['POST'])
def resend_otp():
    """Resend OTP"""
    data = request.get_json()
    
    if not data.get('user_id'):
        return jsonify({'status': 'error', 'message': 'User ID required'}), 400
    
    user_id = data['user_id']
    user = User.query.get(user_id)
    
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404
    
    # Invalidate old OTPs
    OTP.query.filter_by(user_id=user_id, is_used=False).update({'is_used': True})
    
    # Generate new OTP
    otp_code = generate_otp()
    expires_at = datetime.utcnow() + timedelta(minutes=10)
    
    otp = OTP(
        user_id=user.id,
        email=user.email,
        phone=user.phone,
        code=otp_code,
        purpose='registration',
        expires_at=expires_at
    )
    db.session.add(otp)
    db.session.commit()
    
    # Send OTP via SMS
    message = f"Your Cheap4u verification code is {otp_code}. It expires in 10 minutes."
    send_sms(user.phone, message)
    
    return jsonify({
        'status': 'success',
        'message': 'OTP resent successfully',
        'data': {
            'mock_otp': otp_code if not current_app.config['TERMII_API_KEY'] else None
        }
    })

@auth_bp.route('/login', methods=['POST'])
def login():
    """Login user"""
    data = request.get_json()
    
    if not data.get('email') or not data.get('password'):
        return jsonify({'status': 'error', 'message': 'Email and password required'}), 400
    
    email = data['email'].lower()
    password = data['password']
    
    user = User.query.filter_by(email=email).first()
    
    if not user or not user.check_password(password):
        return jsonify({'status': 'error', 'message': 'Invalid credentials'}), 401
    
    if not user.is_verified:
        return jsonify({
            'status': 'error',
            'message': 'Account not verified',
            'requires_verification': True,
            'user_id': user.id
        }), 403
    
    # Update last login
    user.last_login = datetime.utcnow()
    db.session.commit()
    
    # Generate JWT token
    access_token = create_access_token(identity=user.id)
    
    return jsonify({
        'status': 'success',
        'message': 'Login successful',
        'data': {
            'user': user.to_dict(),
            'session_token': access_token
        }
    }) 


