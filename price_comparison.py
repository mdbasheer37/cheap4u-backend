# price_comparison.py — Smart Price Comparison: core logic
#
# Reads live data every call — DataPlan prices, recent Transaction outcomes
# for speed/reliability, and active Coupon rows for promotions — rather
# than maintaining a separate cached/duplicated dataset, so results can
# never drift out of sync with what a purchase would actually cost.
#
# Honest note on scope: Cheap4U charges airtime at face value on every
# network (see cheapdatahub.PROFIT_MARGINS — one flat margin, not
# per-network), so there is no real airtime PRICE to compare across
# networks. compare_airtime() reflects that plainly instead of inventing
# a fake price difference — the real differentiators are speed,
# reliability, and any active promo.

import re
from datetime import datetime, timedelta

from models import db, DataPlan, Transaction
from coupon_models import Coupon
from comparison_models import ComparisonConfig

AIRTIME_NETWORKS = ('MTN', 'Glo', 'Airtel', '9Mobile')   # kept in sync with cheapdatahub.provider_map


def get_config():
    cfg = ComparisonConfig.query.get(1)
    if not cfg:
        cfg = ComparisonConfig(id=1)
        db.session.add(cfg)
        db.session.flush()
    return cfg


# ── helpers ──────────────────────────────────────────────────────────────────

def _parse_size_to_mb(size_str):
    """'1GB' -> 1024, '1.5GB' -> 1536, '500MB' -> 500, '2TB' -> ..."""
    if not size_str:
        return None
    match = re.match(r'([\d.]+)\s*(GB|MB|TB)', str(size_str).strip(), re.IGNORECASE)
    if not match:
        return None
    value, unit = float(match.group(1)), match.group(2).upper()
    if unit == 'TB':
        return value * 1024 * 1024
    if unit == 'GB':
        return value * 1024
    return value


def _normalize(values, lower_is_better=True):
    """Min-max normalize a list of numbers to a 0-1 'goodness' score."""
    clean = [v for v in values if v is not None]
    if not clean or max(clean) == min(clean):
        return {i: (1.0 if v is not None else 0.0) for i, v in enumerate(values)}
    lo, hi = min(clean), max(clean)
    scores = {}
    for i, v in enumerate(values):
        if v is None:
            scores[i] = 0.0
            continue
        norm = (v - lo) / (hi - lo)
        scores[i] = (1 - norm) if lower_is_better else norm
    return scores


def _provider_stats(category, provider_field_value, field_name, cfg):
    """Pulls recent transactions of `category` matching a provider/network
    value stored in Transaction.details[field_name], and computes success
    rate + average processing time in Python (portable across SQLite and
    Postgres — no reliance on a specific JSON query dialect)."""
    since = datetime.utcnow() - timedelta(days=cfg.stats_window_days)
    rows = (
        Transaction.query
        .filter(Transaction.type == category, Transaction.created_at >= since)
        .order_by(Transaction.created_at.desc())
        .limit(cfg.stats_sample_limit * 4)   # over-fetch since we filter by provider in Python
        .all()
    )

    matched = [t for t in rows if (t.details or {}).get(field_name) == provider_field_value]
    matched = matched[:cfg.stats_sample_limit]

    if not matched:
        return {'sample_size': 0, 'success_rate': None, 'avg_processing_time_ms': None}

    successes = [t for t in matched if t.status == 'success']
    success_rate = round(len(successes) / len(matched) * 100, 1)

    timed = [t.details.get('processing_time_ms') for t in matched if (t.details or {}).get('processing_time_ms') is not None]
    avg_time = round(sum(timed) / len(timed)) if timed else None

    return {'sample_size': len(matched), 'success_rate': success_rate, 'avg_processing_time_ms': avg_time}


def _active_promotions(category):
    now = datetime.utcnow()
    coupons = Coupon.query.filter(Coupon.is_active.is_(True)).all()
    result = []
    for c in coupons:
        if c.starts_at and now < c.starts_at:
            continue
        if c.expires_at and now > c.expires_at:
            continue
        if c.specific_user_id is not None:
            continue  # personal coupons aren't a public "promotion" to advertise
        if c.applicable_categories:
            allowed = [x.strip() for x in c.applicable_categories.split(',') if x.strip()]
            if allowed and category not in allowed:
                continue
        result.append({
            'code': c.code, 'discount_type': c.discount_type, 'discount_value': c.discount_value,
            'min_transaction_amount': c.min_transaction_amount,
        })
    return result


# ── data plan comparison ─────────────────────────────────────────────────────

def compare_data_plans(plan_type=None):
    cfg = get_config()
    q = DataPlan.query
    if plan_type:
        q = q.filter_by(plan_type=plan_type)
    plans = q.all()

    rows = []
    for p in plans:
        size_mb = _parse_size_to_mb(p.size)
        price_per_gb = round(p.selling_price / (size_mb / 1024), 2) if size_mb else None
        rows.append({
            'plan_id': p.plan_id, 'provider': p.provider, 'size': p.size,
            'duration': p.duration, 'plan_type': p.plan_type,
            'price': p.selling_price, 'price_per_gb': price_per_gb,
        })

    # per-provider speed/reliability, computed once per distinct provider
    providers = sorted(set(r['provider'] for r in rows))
    provider_stats = {prov: _provider_stats('data', prov, 'provider', cfg) for prov in providers}

    price_values = [r['price_per_gb'] for r in rows]
    price_scores = _normalize(price_values, lower_is_better=True)

    speed_values = [provider_stats[r['provider']]['avg_processing_time_ms'] for r in rows]
    speed_scores = _normalize(speed_values, lower_is_better=True)

    reliability_values = [provider_stats[r['provider']]['success_rate'] for r in rows]
    reliability_scores = _normalize(reliability_values, lower_is_better=False)

    total_weight = (cfg.price_weight + cfg.speed_weight + cfg.reliability_weight) or 1.0

    for i, r in enumerate(rows):
        prov = r['provider']
        r['speed'] = provider_stats[prov]
        composite = (
            price_scores[i] * cfg.price_weight +
            speed_scores[i] * cfg.speed_weight +
            reliability_scores[i] * cfg.reliability_weight
        ) / total_weight
        r['value_score'] = round(composite * 100, 1)

    rows.sort(key=lambda r: r['value_score'], reverse=True)

    cheapest = min((r for r in rows if r['price_per_gb']), key=lambda r: r['price_per_gb'], default=None)
    fastest_provider = min(
        (p for p in providers if provider_stats[p]['avg_processing_time_ms'] is not None),
        key=lambda p: provider_stats[p]['avg_processing_time_ms'], default=None,
    )
    best_value = rows[0] if rows else None

    return {
        'plans': rows,
        'provider_stats': provider_stats,
        'cheapest_plan': cheapest,
        'fastest_provider': fastest_provider,
        'best_value_plan': best_value,
        'promotions': _active_promotions('data'),
    }


# ── airtime comparison ───────────────────────────────────────────────────────

def compare_airtime():
    cfg = get_config()
    rows = []
    for network in AIRTIME_NETWORKS:
        stats = _provider_stats('airtime', network, 'network', cfg)
        rows.append({'network': network, **stats})

    speed_values = [r['avg_processing_time_ms'] for r in rows]
    speed_scores = _normalize(speed_values, lower_is_better=True)
    reliability_values = [r['success_rate'] for r in rows]
    reliability_scores = _normalize(reliability_values, lower_is_better=False)

    speed_weight_total = (cfg.speed_weight + cfg.reliability_weight) or 1.0
    for i, r in enumerate(rows):
        composite = (
            speed_scores[i] * cfg.speed_weight + reliability_scores[i] * cfg.reliability_weight
        ) / speed_weight_total
        r['value_score'] = round(composite * 100, 1)

    rows.sort(key=lambda r: r['value_score'], reverse=True)

    fastest = min(
        (r for r in rows if r['avg_processing_time_ms'] is not None),
        key=lambda r: r['avg_processing_time_ms'], default=None,
    )
    most_reliable = max(
        (r for r in rows if r['success_rate'] is not None),
        key=lambda r: r['success_rate'], default=None,
    )

    return {
        'note': 'Airtime is charged at face value on every network — there is no price difference to compare. '
                'Rankings below are based on recent delivery speed and reliability.',
        'networks': rows,
        'fastest_network': fastest['network'] if fastest else None,
        'most_reliable_network': most_reliable['network'] if most_reliable else None,
        'promotions': _active_promotions('airtime'),
    }
