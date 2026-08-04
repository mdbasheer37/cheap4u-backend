# merchant_routes.py — Merchant Dashboard API
#
# Three blueprints:
#   merchant_bp        /api/merchant/...           (JWT — the merchant, via the app)
#   merchant_admin_bp   /api/admin/merchant/...      (JWT — admin only)
#   merchant_api_bp      /api/merchant-api/v1/...      (X-API-Key header — external systems)

import csv
import io
import logging
from datetime import datetime
from functools import wraps
from flask import Blueprint, request, jsonify, Response, g
from flask_jwt_extended import jwt_required, get_jwt_identity

from models import db, User
from admin import admin_required
import merchant as merchant_service

logger = logging.getLogger(__name__)

merchant_bp = Blueprint('merchant', __name__, url_prefix='/api/merchant')
merchant_admin_bp = Blueprint('merchant_admin', __name__, url_prefix='/api/admin/merchant')
merchant_api_bp = Blueprint('merchant_api', __name__, url_prefix='/api/merchant-api/v1')


def _current_user_id():
    return int(get_jwt_identity())


def _parse_dt(val):
    if not val:
        return None
    try:
        return datetime.fromisoformat(str(val).replace('Z', ''))
    except ValueError:
        return None


# ── API-KEY AUTH (for merchant_api_bp) ──────────────────────────────────────

def api_key_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        user, profile = merchant_service.authenticate_api_key(api_key)
        if not user:
            return jsonify({'status': 'error', 'message': 'Invalid or inactive API key'}), 401
        g.merchant_user = user
        g.merchant_profile = profile
        return f(*args, **kwargs)
    return decorated


# ── MERCHANT-FACING (JWT) ───────────────────────────────────────────────────

@merchant_bp.route('/apply', methods=['POST'])
@jwt_required()
def apply():
    data = request.get_json() or {}
    profile, error = merchant_service.apply_for_merchant(_current_user_id(), data)
    if error:
        return jsonify({'status': 'error', 'message': error}), 400
    return jsonify({'status': 'success', 'message': 'Merchant application submitted for review',
                     'data': profile.to_dict()}), 201


@merchant_bp.route('/profile', methods=['GET'])
@jwt_required()
def profile():
    p = merchant_service.get_profile(_current_user_id())
    if not p:
        return jsonify({'status': 'success', 'data': None})
    return jsonify({'status': 'success', 'data': p.to_dict()})


@merchant_bp.route('/wallet', methods=['GET'])
@jwt_required()
def wallet():
    user = User.query.get(_current_user_id())
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404
    p, error = merchant_service.require_approved_merchant(user.id)
    if error:
        return jsonify({'status': 'error', 'message': error}), 403
    return jsonify({'status': 'success', 'data': {'wallet_balance': round(user.wallet_balance, 2)}})


def _bulk_route(job_type):
    data = request.get_json() or {}
    items = data.get('items')
    if not isinstance(items, list):
        return jsonify({'status': 'error', 'message': 'items must be a list'}), 400
    job, error = merchant_service.process_bulk_job(_current_user_id(), job_type, items)
    if error:
        return jsonify({'status': 'error', 'message': error}), 400
    return jsonify({'status': 'success', 'message': 'Bulk job processed', 'data': job.to_dict()}), 201


@merchant_bp.route('/bulk/airtime', methods=['POST'])
@jwt_required()
def bulk_airtime():
    return _bulk_route('airtime')


@merchant_bp.route('/bulk/data', methods=['POST'])
@jwt_required()
def bulk_data():
    return _bulk_route('data')


@merchant_bp.route('/bulk/electricity', methods=['POST'])
@jwt_required()
def bulk_electricity():
    return _bulk_route('electricity')


@merchant_bp.route('/bulk/cable-tv', methods=['POST'])
@jwt_required()
def bulk_cable_tv():
    return _bulk_route('cable_tv')


@merchant_bp.route('/bulk/exam-pin', methods=['POST'])
@jwt_required()
def bulk_exam_pin():
    return _bulk_route('exam_pin')


@merchant_bp.route('/bulk/jobs', methods=['GET'])
@jwt_required()
def bulk_jobs():
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 20, type=int), 100)
    rows, total, pages = merchant_service.list_bulk_jobs(_current_user_id(), page, per_page)
    return jsonify({'status': 'success', 'data': {'jobs': rows, 'total': total, 'pages': pages, 'page': page}})


@merchant_bp.route('/bulk/jobs/<int:job_id>', methods=['GET'])
@jwt_required()
def bulk_job_detail(job_id):
    job, items, error = merchant_service.get_bulk_job(job_id, _current_user_id())
    if error:
        return jsonify({'status': 'error', 'message': error}), 404
    return jsonify({'status': 'success', 'data': {
        'job': job.to_dict(), 'items': [i.to_dict() for i in items],
    }})


@merchant_bp.route('/analytics/profit', methods=['GET'])
@jwt_required()
def profit_analytics():
    date_from = _parse_dt(request.args.get('date_from'))
    date_to = _parse_dt(request.args.get('date_to'))
    data = merchant_service.get_profit_analytics(_current_user_id(), date_from, date_to)
    return jsonify({'status': 'success', 'data': data})


@merchant_bp.route('/reports/transactions', methods=['GET'])
@jwt_required()
def transaction_report():
    date_from = _parse_dt(request.args.get('date_from'))
    date_to = _parse_dt(request.args.get('date_to'))
    category = request.args.get('category')
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 200)
    rows, total, pages = merchant_service.get_transaction_report(
        _current_user_id(), date_from, date_to, category, page, per_page)
    return jsonify({'status': 'success',
                     'data': {'transactions': rows, 'total': total, 'pages': pages, 'page': page}})


@merchant_bp.route('/reports/transactions/export', methods=['GET'])
@jwt_required()
def export_transaction_report():
    date_from = _parse_dt(request.args.get('date_from'))
    date_to = _parse_dt(request.args.get('date_to'))
    category = request.args.get('category')
    rows, _, _ = merchant_service.get_transaction_report(
        _current_user_id(), date_from, date_to, category, page=1, per_page=200)

    # Pull the rest beyond the first page too, up to a sane export cap
    all_rows = list(rows)
    page = 2
    while len(all_rows) < 5000:
        more, total, _ = merchant_service.get_transaction_report(
            _current_user_id(), date_from, date_to, category, page=page, per_page=200)
        if not more:
            break
        all_rows.extend(more)
        page += 1
        if len(all_rows) >= total:
            break

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['Date', 'Reference', 'Type', 'Amount', 'Profit', 'Status'])
    for t in all_rows:
        writer.writerow([
            t.get('created_at', ''), t.get('reference', ''), t.get('type', ''),
            f"{t.get('amount', 0):,.2f}", f"{t.get('profit', 0):,.2f}", t.get('status', ''),
        ])

    return Response(
        buf.getvalue(), mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=merchant_transactions.csv'},
    )


@merchant_bp.route('/api-key/regenerate', methods=['POST'])
@jwt_required()
def regenerate_api_key():
    key, error = merchant_service.regenerate_api_key(_current_user_id())
    if error:
        return jsonify({'status': 'error', 'message': error}), 403
    return jsonify({'status': 'success', 'message': 'API key regenerated', 'data': {'api_key': key}})


# ── ADMIN ─────────────────────────────────────────────────────────────────────

@merchant_admin_bp.route('/applications', methods=['GET'])
@admin_required
def list_applications():
    status = request.args.get('status')
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 30, type=int), 100)
    rows, total, pages = merchant_service.list_applications(status, page, per_page)
    return jsonify({'status': 'success',
                     'data': {'applications': rows, 'total': total, 'pages': pages, 'page': page}})


@merchant_admin_bp.route('/applications/<int:profile_id>/approve', methods=['POST'])
@admin_required
def approve(profile_id):
    admin_id = _current_user_id()
    p, error = merchant_service.approve_merchant(profile_id, admin_id)
    if error:
        return jsonify({'status': 'error', 'message': error}), 404
    return jsonify({'status': 'success', 'message': 'Merchant approved', 'data': p.to_dict(include_api_key=True)})


@merchant_admin_bp.route('/applications/<int:profile_id>/reject', methods=['POST'])
@admin_required
def reject(profile_id):
    data = request.get_json() or {}
    p, error = merchant_service.reject_merchant(profile_id, _current_user_id(), reason=data.get('reason'))
    if error:
        return jsonify({'status': 'error', 'message': error}), 404
    return jsonify({'status': 'success', 'message': 'Merchant application rejected', 'data': p.to_dict()})


@merchant_admin_bp.route('/applications/<int:profile_id>/suspend', methods=['POST'])
@admin_required
def suspend(profile_id):
    data = request.get_json() or {}
    p, error = merchant_service.suspend_merchant(profile_id, _current_user_id(), reason=data.get('reason'))
    if error:
        return jsonify({'status': 'error', 'message': error}), 404
    return jsonify({'status': 'success', 'message': 'Merchant suspended', 'data': p.to_dict()})


@merchant_admin_bp.route('/applications/<int:profile_id>/reactivate', methods=['POST'])
@admin_required
def reactivate(profile_id):
    p, error = merchant_service.reactivate_merchant(profile_id, _current_user_id())
    if error:
        return jsonify({'status': 'error', 'message': error}), 404
    return jsonify({'status': 'success', 'message': 'Merchant reactivated', 'data': p.to_dict()})


@merchant_admin_bp.route('/stats', methods=['GET'])
@admin_required
def stats():
    return jsonify({'status': 'success', 'data': merchant_service.get_platform_stats()})


@merchant_admin_bp.route('/<int:user_id>/transactions', methods=['GET'])
@admin_required
def merchant_transactions(user_id):
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 200)
    rows, total, pages = merchant_service.get_transaction_report(user_id, page=page, per_page=per_page)
    return jsonify({'status': 'success',
                     'data': {'transactions': rows, 'total': total, 'pages': pages, 'page': page}})


# ── EXTERNAL API (API-key auth) ─────────────────────────────────────────────
# A lightweight surface so a merchant's own system can top up single
# transactions programmatically. Kept separate from the JWT-based bulk
# upload endpoints above, which are for the in-app dashboard.

@merchant_api_bp.route('/airtime', methods=['POST'])
@api_key_required
def api_airtime():
    from cheapdatahub import buy_airtime
    data = request.get_json() or {}
    result = buy_airtime(data.get('network'), data.get('phone'), float(data.get('amount', 0)),
                          g.merchant_user.email)
    return jsonify(result), (200 if result.get('status') == 'success' else 400)


@merchant_api_bp.route('/data', methods=['POST'])
@api_key_required
def api_data():
    from cheapdatahub import buy_data
    data = request.get_json() or {}
    result = buy_data(data.get('plan_id'), data.get('phone'), g.merchant_user.email)
    return jsonify(result), (200 if result.get('status') == 'success' else 400)


@merchant_api_bp.route('/balance', methods=['GET'])
@api_key_required
def api_balance():
    return jsonify({'status': 'success', 'data': {'wallet_balance': round(g.merchant_user.wallet_balance, 2)}})
