from flask import Blueprint, jsonify
from models import DataPlan, CablePlan, ElectricityProvider

plans_bp = Blueprint('plans', __name__, url_prefix='/api/plans')

@plans_bp.route('/data', methods=['GET'])
def get_data_plans():
    plans = DataPlan.query.all()
    return jsonify({
        'status': 'success',
        'data': [{
            'plan_id': p.plan_id,
            'provider': p.provider,
            'size': p.size,
            'duration': p.duration,
            'selling_price': p.selling_price,
            'cost_price': p.cost_price,
            'type': p.plan_type or 'Gifting'
        } for p in plans]
    })

@plans_bp.route('/cable', methods=['GET'])
def get_cable_plans():
    plans = CablePlan.query.all()
    return jsonify({
        'status': 'success',
        'data': [{
            'plan_id': p.plan_id,
            'provider': p.provider,
            'plan_name': p.plan_name,
            'selling_price': p.selling_price,
            'cost_price': p.cost_price
        } for p in plans]
    })

@plans_bp.route('/electricity-providers', methods=['GET'])
def get_electricity_providers():
    providers = ElectricityProvider.query.all()
    return jsonify({
        'status': 'success',
        'data': [{
            'provider_id': p.provider_id,
            'name': p.name,
            'discount_percent': p.discount_percent
        } for p in providers]
    })
