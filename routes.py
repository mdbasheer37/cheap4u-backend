# routes.py  (vtpass blueprint)
import bcrypt
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from models import db, User
from cheapdatahub import buy_airtime, buy_data, buy_electricity, buy_cable_tv
from vtunaija import buy_exam_pin  # exam pins come from vtunaija only

vtpass_bp = Blueprint('vtpass', __name__, url_prefix='/api/vtpass')


def _get_verified_user(user_id):
    """Return user if active, else return (None, error_response)."""
    user = User.query.get(user_id)
    if not user:
        return None, (jsonify({'status': 'error', 'message': 'User not found'}), 404)
    if not user.is_active:
        return None, (jsonify({'status': 'error', 'message': 'Account is blocked'}), 403)
    return user, None


def _verify_pin(user, pin):
    """Return True if PIN matches, False otherwise."""
    if not pin:
        return False
    if not user.transaction_pin_hash:
        return False
    return bcrypt.checkpw(pin.encode('utf-8'), user.transaction_pin_hash.encode('utf-8'))


@vtpass_bp.route('/airtime', methods=['POST'])
@jwt_required()
def purchase_airtime():
    """Purchase airtime with PIN verification."""
    user_id = get_jwt_identity()
    user, err = _get_verified_user(user_id)
    if err:
        return err

    data = request.get_json()
    pin = data.get('pin')
    if not pin:
        return jsonify({'status': 'error', 'message': 'Transaction PIN required'}), 400
    if not _verify_pin(user, pin):
        return jsonify({'status': 'error', 'message': 'Incorrect transaction PIN'}), 401

    network = data.get('network')
    phone = data.get('phone')
    amount = data.get('amount')
    user_email = user.email  # always use the authenticated user's email

    result = buy_airtime(network, phone, amount, user_email)
    return jsonify(result)


@vtpass_bp.route('/data', methods=['POST'])
@jwt_required()
def purchase_data():
    """Purchase data bundle with PIN verification."""
    user_id = get_jwt_identity()
    user, err = _get_verified_user(user_id)
    if err:
        return err

    data = request.get_json()
    pin = data.get('pin')
    if not pin:
        return jsonify({'status': 'error', 'message': 'Transaction PIN required'}), 400
    if not _verify_pin(user, pin):
        return jsonify({'status': 'error', 'message': 'Incorrect transaction PIN'}), 401

    # Fixed: buy_data(plan_id, phone, user_email) — not network/plan_code
    plan_id = data.get('plan_id')
    phone = data.get('phone')

    if not plan_id:
        return jsonify({'status': 'error', 'message': 'plan_id is required'}), 400

    result = buy_data(plan_id, phone, user.email)
    return jsonify(result)


@vtpass_bp.route('/electricity', methods=['POST'])
@jwt_required()
def purchase_electricity():
    """Purchase electricity with PIN verification."""
    user_id = get_jwt_identity()
    user, err = _get_verified_user(user_id)
    if err:
        return err

    data = request.get_json()
    pin = data.get('pin')
    if not pin:
        return jsonify({'status': 'error', 'message': 'Transaction PIN required'}), 400
    if not _verify_pin(user, pin):
        return jsonify({'status': 'error', 'message': 'Incorrect transaction PIN'}), 401

    disco = data.get('disco')
    meter_number = data.get('meter_number')
    meter_type = data.get('meter_type')
    amount = data.get('amount')
    phone = data.get('phone')

    result = buy_electricity(disco, meter_number, meter_type, amount, phone, user.email)
    return jsonify(result)


@vtpass_bp.route('/cable-tv', methods=['POST'])
@jwt_required()
def purchase_cable_tv():
    """Purchase cable TV subscription with PIN verification."""
    user_id = get_jwt_identity()
    user, err = _get_verified_user(user_id)
    if err:
        return err

    data = request.get_json()
    pin = data.get('pin')
    if not pin:
        return jsonify({'status': 'error', 'message': 'Transaction PIN required'}), 400
    if not _verify_pin(user, pin):
        return jsonify({'status': 'error', 'message': 'Incorrect transaction PIN'}), 401

    # Fixed: buy_cable_tv(plan_id, smartcard, user_email) — not provider/package/amount
    plan_id = data.get('plan_id')
    smartcard = data.get('smartcard')

    if not plan_id:
        return jsonify({'status': 'error', 'message': 'plan_id is required'}), 400

    result = buy_cable_tv(plan_id, smartcard, user.email)
    return jsonify(result)


@vtpass_bp.route('/exam-pins', methods=['POST'])
@jwt_required()
def purchase_exam_pins():
    """Purchase exam PINs with PIN verification."""
    user_id = get_jwt_identity()
    user, err = _get_verified_user(user_id)
    if err:
        return err

    data = request.get_json()
    pin = data.get('pin')
    if not pin:
        return jsonify({'status': 'error', 'message': 'Transaction PIN required'}), 400

    if not user.transaction_pin_hash:
        return jsonify({'status': 'error', 'message': 'Please set a transaction PIN first'}), 400
    if not _verify_pin(user, pin):
        return jsonify({'status': 'error', 'message': 'Incorrect transaction PIN'}), 401

    exam_type = data.get('exam_type')
    quantity = data.get('quantity')
    selling_price = data.get('selling_price')

    if not exam_type or not quantity:
        return jsonify({'status': 'error', 'message': 'Exam type and quantity required'}), 400
    if quantity < 1 or quantity > 10:
        return jsonify({'status': 'error', 'message': 'Quantity must be between 1 and 10'}), 400
    if selling_price and user.wallet_balance < float(selling_price):
        return jsonify({
            'status': 'error',
            'message': f'Insufficient balance. Available: ₦{user.wallet_balance:,.2f}'
        }), 400

    result = buy_exam_pin(exam_type, quantity, user.email, selling_price)
    return jsonify(result)
