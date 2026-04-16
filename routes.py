import bcrypt
from vtunaija import buy_exam_pin 
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from models import db, User
from cheapdatahub import buy_airtime, buy_data, buy_electricity, buy_cable_tv, buy_exam_pin

vtpass_bp = Blueprint('vtpass', __name__, url_prefix='/api/vtpass')

@vtpass_bp.route('/airtime', methods=['POST'])
@jwt_required()
def purchase_airtime():
    """Purchase airtime with PIN verification"""
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

    if not user.is_active:
    return jsonify({'status': 'error', 'message': 'Account is blocked'}), 403 

    data = request.get_json()
    pin = data.get('pin')
    if not pin:
        return jsonify({'status': 'error', 'message': 'Transaction PIN required'}), 400

    # Verify PIN
    if not user.transaction_pin_hash or not bcrypt.checkpw(pin.encode('utf-8'), user.transaction_pin_hash.encode('utf-8')):
        return jsonify({'status': 'error', 'message': 'Incorrect transaction PIN'}), 401

    # Process purchase
    network = data.get('network')
    phone = data.get('phone')
    amount = data.get('amount')
    user_email = data.get('user_email', user.email)

    result = buy_airtime(network, phone, amount, user_email)
    return jsonify(result)


@vtpass_bp.route('/data', methods=['POST'])
@jwt_required()
def purchase_data():
    """Purchase data bundle with PIN verification"""
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

    data = request.get_json()
    pin = data.get('pin')
    if not pin:
        return jsonify({'status': 'error', 'message': 'Transaction PIN required'}), 400

    # Verify PIN
    if not user.transaction_pin_hash or not bcrypt.checkpw(pin.encode('utf-8'), user.transaction_pin_hash.encode('utf-8')):
        return jsonify({'status': 'error', 'message': 'Incorrect transaction PIN'}), 401

    # Process purchase
    network = data.get('network')
    phone = data.get('phone')
    plan_code = data.get('plan_code')
    user_email = data.get('user_email', user.email)

    result = buy_data(network, phone, plan_code, user_email)
    return jsonify(result)


@vtpass_bp.route('/electricity', methods=['POST'])
@jwt_required()
def purchase_electricity():
    """Purchase electricity with PIN verification"""
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

    data = request.get_json()
    pin = data.get('pin')
    if not pin:
        return jsonify({'status': 'error', 'message': 'Transaction PIN required'}), 400

    # Verify PIN
    if not user.transaction_pin_hash or not bcrypt.checkpw(pin.encode('utf-8'), user.transaction_pin_hash.encode('utf-8')):
        return jsonify({'status': 'error', 'message': 'Incorrect transaction PIN'}), 401

    # Process purchase
    disco = data.get('disco')
    meter_number = data.get('meter_number')
    meter_type = data.get('meter_type')
    amount = data.get('amount')
    phone = data.get('phone')
    user_email = data.get('user_email', user.email)

    result = buy_electricity(disco, meter_number, meter_type, amount, phone, user_email)
    return jsonify(result)


@vtpass_bp.route('/cable-tv', methods=['POST'])
@jwt_required()
def purchase_cable_tv():
    """Purchase cable TV subscription with PIN verification"""
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

    data = request.get_json()
    pin = data.get('pin')
    if not pin:
        return jsonify({'status': 'error', 'message': 'Transaction PIN required'}), 400

    # Verify PIN
    if not user.transaction_pin_hash or not bcrypt.checkpw(pin.encode('utf-8'), user.transaction_pin_hash.encode('utf-8')):
        return jsonify({'status': 'error', 'message': 'Incorrect transaction PIN'}), 401

    # Process purchase
    provider = data.get('provider')
    package = data.get('package')
    smartcard = data.get('smartcard')
    amount = data.get('amount')
    user_email = data.get('user_email', user.email)

    result = buy_cable_tv(provider, package, smartcard, amount, user_email)
    return jsonify(result)



@vtpass_bp.route('/exam-pins', methods=['POST'])
@jwt_required()
def purchase_exam_pins():
    """Purchase exam PINs with PIN verification."""
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

    data = request.get_json()
    pin = data.get('pin')
    if not pin:
        return jsonify({'status': 'error', 'message': 'Transaction PIN required'}), 400

    # Verify transaction PIN
    if not user.transaction_pin_hash:
        return jsonify({'status': 'error', 'message': 'Please set a transaction PIN first'}), 400
    
    if not bcrypt.checkpw(pin.encode('utf-8'), user.transaction_pin_hash.encode('utf-8')):
        return jsonify({'status': 'error', 'message': 'Incorrect transaction PIN'}), 401

    # Get exam purchase details
    exam_type = data.get('exam_type')  # WAEC, NECO, JAMB, NABTEB
    quantity = data.get('quantity')
    selling_price = data.get('selling_price')  # Price customer paid
    user_email = data.get('user_email', user.email)
    
    # Validate inputs
    if not exam_type or not quantity:
        return jsonify({'status': 'error', 'message': 'Exam type and quantity required'}), 400
    
    if quantity < 1 or quantity > 10:
        return jsonify({'status': 'error', 'message': 'Quantity must be between 1 and 10'}), 400
    
    # Check if user has enough balance
    if selling_price and user.wallet_balance < float(selling_price):
        return jsonify({
            'status': 'error', 
            'message': f'Insufficient balance. Available: ₦{user.wallet_balance:,.2f}'
        }), 400
    
    # Purchase exam PIN
    result = buy_exam_pin(exam_type, quantity, user_email, selling_price)
    
    return jsonify(result)
