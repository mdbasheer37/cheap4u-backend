# card_routes.py — Virtual Dollar Card API
#
# Two blueprints:
#   card_bp        /api/cards/...        (any logged-in user)
#   card_admin_bp   /api/admin/cards/...  (admin only)

import logging
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from models import db
from admin import admin_required
import card as card_service

logger = logging.getLogger(__name__)

card_bp = Blueprint('card', __name__, url_prefix='/api/cards')
card_admin_bp = Blueprint('card_admin', __name__, url_prefix='/api/admin/cards')


def _current_user_id():
    return int(get_jwt_identity())


# ── USER-FACING ──────────────────────────────────────────────────────────────

@card_bp.route('/config', methods=['GET'])
@jwt_required()
def config():
    cfg = card_service.get_config()
    db.session.commit()
    return jsonify({'status': 'success', 'data': cfg.to_dict()})


@card_bp.route('', methods=['GET'])
@jwt_required()
def list_cards():
    cards = card_service.list_user_cards(_current_user_id())
    return jsonify({'status': 'success', 'data': [c.to_dict() for c in cards]})


@card_bp.route('', methods=['POST'])
@jwt_required()
def create_card():
    data = request.get_json() or {}
    if 'funding_amount_usd' not in data:
        return jsonify({'status': 'error', 'message': 'funding_amount_usd is required'}), 400
    card, error = card_service.create_card(_current_user_id(), data.get('funding_amount_usd'))
    if error:
        return jsonify({'status': 'error', 'message': error}), 400
    return jsonify({'status': 'success', 'message': 'Card created', 'data': card.to_dict()}), 201


@card_bp.route('/<int:card_id>', methods=['GET'])
@jwt_required()
def get_card(card_id):
    card = card_service.get_user_card(_current_user_id(), card_id)
    if not card:
        return jsonify({'status': 'error', 'message': 'Card not found'}), 404
    return jsonify({'status': 'success', 'data': card.to_dict()})


@card_bp.route('/<int:card_id>/fund', methods=['POST'])
@jwt_required()
def fund_card(card_id):
    data = request.get_json() or {}
    if 'amount_usd' not in data:
        return jsonify({'status': 'error', 'message': 'amount_usd is required'}), 400
    card, error = card_service.fund_card(_current_user_id(), card_id, data.get('amount_usd'))
    if error:
        return jsonify({'status': 'error', 'message': error}), 400
    return jsonify({'status': 'success', 'message': 'Card funded', 'data': card.to_dict()})


@card_bp.route('/<int:card_id>/freeze', methods=['POST'])
@jwt_required()
def freeze_card(card_id):
    card, error = card_service.freeze_card(_current_user_id(), card_id)
    if error:
        return jsonify({'status': 'error', 'message': error}), 400
    return jsonify({'status': 'success', 'message': 'Card frozen', 'data': card.to_dict()})


@card_bp.route('/<int:card_id>/unfreeze', methods=['POST'])
@jwt_required()
def unfreeze_card(card_id):
    card, error = card_service.unfreeze_card(_current_user_id(), card_id)
    if error:
        return jsonify({'status': 'error', 'message': error}), 400
    return jsonify({'status': 'success', 'message': 'Card unfrozen', 'data': card.to_dict()})


@card_bp.route('/<int:card_id>', methods=['DELETE'])
@jwt_required()
def delete_card(card_id):
    card, error = card_service.delete_card(_current_user_id(), card_id)
    if error:
        return jsonify({'status': 'error', 'message': error}), 400
    return jsonify({'status': 'success', 'message': 'Card deleted and balance refunded to wallet',
                     'data': card.to_dict()})


@card_bp.route('/<int:card_id>/transactions', methods=['GET'])
@jwt_required()
def card_history(card_id):
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 30, type=int), 100)
    card, entries, total, error = card_service.get_card_history(_current_user_id(), card_id, page, per_page)
    if error:
        return jsonify({'status': 'error', 'message': error}), 404
    return jsonify({'status': 'success', 'data': {'card': card.to_dict(), 'transactions': entries, 'total': total}})


# ── ADMIN ─────────────────────────────────────────────────────────────────────

@card_admin_bp.route('/config', methods=['GET'])
@admin_required
def admin_get_config():
    cfg = card_service.get_config()
    db.session.commit()
    return jsonify({'status': 'success', 'data': cfg.to_dict()})


@card_admin_bp.route('/config', methods=['POST'])
@admin_required
def admin_update_config():
    data = request.get_json() or {}
    cfg = card_service.get_config()

    if 'is_enabled' in data:
        cfg.is_enabled = bool(data['is_enabled'])
    if 'provider_name' in data:
        cfg.provider_name = data['provider_name']

    for field in ('usd_to_ngn_rate', 'card_creation_fee_usd', 'min_funding_usd', 'max_card_balance_usd'):
        if field in data:
            try:
                val = float(data[field])
                if val < 0:
                    raise ValueError
                setattr(cfg, field, val)
            except (TypeError, ValueError):
                return jsonify({'status': 'error', 'message': f'Invalid value for {field}'}), 400

    db.session.commit()
    return jsonify({'status': 'success', 'message': 'Card settings updated', 'data': cfg.to_dict()})


@card_admin_bp.route('', methods=['GET'])
@admin_required
def admin_list_cards():
    status = request.args.get('status')
    search = request.args.get('search', '').strip() or None
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 30, type=int), 100)
    rows, total, pages = card_service.admin_list_cards(status, search, page, per_page)
    return jsonify({'status': 'success', 'data': {'cards': rows, 'total': total, 'pages': pages, 'page': page}})


@card_admin_bp.route('/<int:card_id>/freeze', methods=['POST'])
@admin_required
def admin_freeze(card_id):
    card, error = card_service.admin_freeze_card(card_id, int(get_jwt_identity()))
    if error:
        return jsonify({'status': 'error', 'message': error}), 400
    return jsonify({'status': 'success', 'message': 'Card frozen by admin', 'data': card.to_dict()})


@card_admin_bp.route('/<int:card_id>/terminate', methods=['POST'])
@admin_required
def admin_terminate(card_id):
    card, error = card_service.admin_terminate_card(card_id, int(get_jwt_identity()))
    if error:
        return jsonify({'status': 'error', 'message': error}), 400
    return jsonify({'status': 'success', 'message': 'Card terminated by admin, balance refunded',
                     'data': card.to_dict()})


@card_admin_bp.route('/stats', methods=['GET'])
@admin_required
def admin_stats():
    return jsonify({'status': 'success', 'data': card_service.get_platform_stats()})
