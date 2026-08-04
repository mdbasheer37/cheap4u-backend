# card.py — Virtual Dollar Card: core business logic
#
# All NGN money movement (wallet debit/credit) happens here, in one place,
# regardless of which provider is configured — card_provider.py only
# knows about USD card operations. This keeps the wallet-safety logic
# (balance checks, atomic commits) identical no matter who issues the card.

import logging
from datetime import datetime

from models import db, User, Transaction
from card_models import CardConfig, VirtualCard, CardTransaction
from card_provider import get_provider, CardProviderError

logger = logging.getLogger(__name__)


def get_config():
    cfg = CardConfig.query.get(1)
    if not cfg:
        cfg = CardConfig(id=1)
        db.session.add(cfg)
        db.session.flush()
    return cfg


def _provider():
    return get_provider(get_config().provider_name)


def _ngn_for_usd(amount_usd, rate):
    return round(amount_usd * rate, 2)


# ── card lifecycle ───────────────────────────────────────────────────────────

def list_user_cards(user_id):
    return VirtualCard.query.filter_by(user_id=user_id).order_by(VirtualCard.created_at.desc()).all()


def get_user_card(user_id, card_id):
    return VirtualCard.query.filter_by(id=card_id, user_id=user_id).first()


def create_card(user_id, funding_amount_usd):
    cfg = get_config()
    if not cfg.is_enabled:
        return None, 'Virtual dollar cards are currently unavailable'

    try:
        funding_amount_usd = round(float(funding_amount_usd), 2)
    except (TypeError, ValueError):
        return None, 'Invalid funding amount'

    if funding_amount_usd < cfg.min_funding_usd:
        return None, f'Minimum initial funding is ${cfg.min_funding_usd:,.2f}'
    if funding_amount_usd > cfg.max_card_balance_usd:
        return None, f'Maximum card balance is ${cfg.max_card_balance_usd:,.2f}'

    user = User.query.get(user_id)
    if not user:
        return None, 'User not found'

    total_usd = round(funding_amount_usd + cfg.card_creation_fee_usd, 2)
    total_ngn = _ngn_for_usd(total_usd, cfg.usd_to_ngn_rate)

    if user.wallet_balance < total_ngn:
        return None, (f'Insufficient balance. You need ₦{total_ngn:,.2f} '
                       f'(${funding_amount_usd:,.2f} funding + ${cfg.card_creation_fee_usd:,.2f} fee). '
                       f'Available: ₦{user.wallet_balance:,.2f}')

    try:
        result = _provider().create_card(user, funding_amount_usd)
    except CardProviderError as e:
        return None, f'Card issuer declined: {e}'
    except Exception:
        logger.exception('[Card] provider.create_card failed')
        return None, 'Card provider is temporarily unavailable. Please try again.'

    user.wallet_balance = round(user.wallet_balance - total_ngn, 2)

    card = VirtualCard(
        user_id=user_id, provider=get_config().provider_name,
        provider_card_id=result['provider_card_id'],
        card_number_masked=result['card_number_masked'],
        card_brand=result.get('card_brand', 'Visa'),
        expiry_month=result['expiry_month'], expiry_year=result['expiry_year'],
        cardholder_name=result['cardholder_name'],
        currency='USD', balance=result['balance'], status='active',
    )
    db.session.add(card)
    db.session.flush()

    db.session.add(CardTransaction(
        card_id=card.id, user_id=user_id, type='funding',
        amount=funding_amount_usd, balance_after=card.balance,
        description='Initial card funding',
    ))

    # Log the NGN side against the main Transaction table so it shows up
    # in the user's normal wallet history too.
    db.session.add(Transaction(
        user_id=user_id, reference=f'CARD-NEW-{card.id}-{int(datetime.utcnow().timestamp())}',
        type='virtual_card', service_type='virtual_card', amount=total_ngn,
        profit=0.0, status='success',
        details={'card_id': card.id, 'action': 'create',
                 'funding_usd': funding_amount_usd, 'fee_usd': cfg.card_creation_fee_usd},
    ))

    db.session.commit()
    return card, None


def fund_card(user_id, card_id, amount_usd):
    cfg = get_config()
    card = get_user_card(user_id, card_id)
    if not card:
        return None, 'Card not found'
    if card.status != 'active':
        return None, f'Card is {card.status} and cannot be funded'

    try:
        amount_usd = round(float(amount_usd), 2)
    except (TypeError, ValueError):
        return None, 'Invalid amount'
    if amount_usd <= 0:
        return None, 'Amount must be greater than zero'
    if card.balance + amount_usd > cfg.max_card_balance_usd:
        return None, f'This would exceed the maximum card balance of ${cfg.max_card_balance_usd:,.2f}'

    amount_ngn = _ngn_for_usd(amount_usd, cfg.usd_to_ngn_rate)
    user = User.query.get(user_id)
    if user.wallet_balance < amount_ngn:
        return None, f'Insufficient balance. Need ₦{amount_ngn:,.2f}, available ₦{user.wallet_balance:,.2f}'

    try:
        _provider().fund_card(card.provider_card_id, amount_usd)
    except CardProviderError as e:
        return None, f'Card issuer declined funding: {e}'
    except Exception:
        logger.exception('[Card] provider.fund_card failed')
        return None, 'Card provider is temporarily unavailable. Please try again.'

    user.wallet_balance = round(user.wallet_balance - amount_ngn, 2)
    card.balance = round(card.balance + amount_usd, 2)

    db.session.add(CardTransaction(
        card_id=card.id, user_id=user_id, type='funding',
        amount=amount_usd, balance_after=card.balance, description='Card top-up',
    ))
    db.session.add(Transaction(
        user_id=user_id, reference=f'CARD-FUND-{card.id}-{int(datetime.utcnow().timestamp())}',
        type='virtual_card', service_type='virtual_card', amount=amount_ngn,
        profit=0.0, status='success',
        details={'card_id': card.id, 'action': 'fund', 'amount_usd': amount_usd},
    ))
    db.session.commit()
    return card, None


def freeze_card(user_id, card_id):
    card = get_user_card(user_id, card_id)
    if not card:
        return None, 'Card not found'
    if card.status == 'terminated':
        return None, 'Card has been deleted'
    if card.status == 'frozen':
        return card, None

    try:
        _provider().freeze_card(card.provider_card_id)
    except CardProviderError as e:
        return None, f'Card issuer declined: {e}'
    except Exception:
        logger.exception('[Card] provider.freeze_card failed')
        return None, 'Card provider is temporarily unavailable. Please try again.'

    card.status = 'frozen'
    db.session.commit()
    return card, None


def unfreeze_card(user_id, card_id):
    card = get_user_card(user_id, card_id)
    if not card:
        return None, 'Card not found'
    if card.status == 'terminated':
        return None, 'Card has been deleted'
    if card.status == 'active':
        return card, None

    try:
        _provider().unfreeze_card(card.provider_card_id)
    except CardProviderError as e:
        return None, f'Card issuer declined: {e}'
    except Exception:
        logger.exception('[Card] provider.unfreeze_card failed')
        return None, 'Card provider is temporarily unavailable. Please try again.'

    card.status = 'active'
    db.session.commit()
    return card, None


def delete_card(user_id, card_id):
    """Terminates the card and refunds any remaining balance to the
    user's naira wallet, exactly like closing a real prepaid card."""
    cfg = get_config()
    card = get_user_card(user_id, card_id)
    if not card:
        return None, 'Card not found'
    if card.status == 'terminated':
        return None, 'Card is already deleted'

    try:
        remaining_usd = _provider().terminate_card(card.provider_card_id)
    except CardProviderError as e:
        return None, f'Card issuer declined: {e}'
    except Exception:
        logger.exception('[Card] provider.terminate_card failed')
        return None, 'Card provider is temporarily unavailable. Please try again.'

    remaining_usd = remaining_usd if remaining_usd is not None else card.balance
    refund_ngn = _ngn_for_usd(remaining_usd, cfg.usd_to_ngn_rate)

    user = User.query.get(user_id)
    if refund_ngn > 0:
        user.wallet_balance = round(user.wallet_balance + refund_ngn, 2)

    card.status = 'terminated'
    card.terminated_at = datetime.utcnow()
    card.balance = 0.0

    if remaining_usd > 0:
        db.session.add(CardTransaction(
            card_id=card.id, user_id=user_id, type='withdrawal',
            amount=remaining_usd, balance_after=0.0, description='Card closed — balance refunded',
        ))
        db.session.add(Transaction(
            user_id=user_id, reference=f'CARD-CLOSE-{card.id}-{int(datetime.utcnow().timestamp())}',
            type='virtual_card', service_type='virtual_card', amount=refund_ngn,
            profit=0.0, status='success',
            details={'card_id': card.id, 'action': 'terminate', 'refunded_usd': remaining_usd},
        ))

    db.session.commit()
    return card, None


def get_card_history(user_id, card_id, page=1, per_page=30):
    card = get_user_card(user_id, card_id)
    if not card:
        return None, None, None, 'Card not found'
    per_page = min(per_page, 100)
    q = CardTransaction.query.filter_by(card_id=card_id).order_by(CardTransaction.created_at.desc())
    total = q.count()
    rows = q.limit(per_page).offset((page - 1) * per_page).all()
    pages = (total + per_page - 1) // per_page if per_page else 1
    return card, [r.to_dict() for r in rows], total, None


# ── admin ────────────────────────────────────────────────────────────────────

def admin_list_cards(status=None, search=None, page=1, per_page=30):
    per_page = min(per_page, 100)
    q = db.session.query(VirtualCard, User).join(User, User.id == VirtualCard.user_id)
    if status:
        q = q.filter(VirtualCard.status == status)
    if search:
        like = f'%{search}%'
        q = q.filter(db.or_(User.name.ilike(like), User.email.ilike(like)))
    q = q.order_by(VirtualCard.created_at.desc())
    total = q.count()
    rows = q.limit(per_page).offset((page - 1) * per_page).all()
    pages = (total + per_page - 1) // per_page if per_page else 1
    result = []
    for card, user in rows:
        d = card.to_dict()
        d['user_name'] = user.name
        d['user_email'] = user.email
        result.append(d)
    return result, total, pages


def admin_freeze_card(card_id, admin_id):
    card = VirtualCard.query.get(card_id)
    if not card:
        return None, 'Card not found'
    return freeze_card(card.user_id, card_id)


def admin_terminate_card(card_id, admin_id):
    card = VirtualCard.query.get(card_id)
    if not card:
        return None, 'Card not found'
    return delete_card(card.user_id, card_id)


def get_platform_stats():
    total_cards = VirtualCard.query.count()
    active = VirtualCard.query.filter_by(status='active').count()
    frozen = VirtualCard.query.filter_by(status='frozen').count()
    terminated = VirtualCard.query.filter_by(status='terminated').count()

    total_balance_usd = (
        db.session.query(db.func.coalesce(db.func.sum(VirtualCard.balance), 0.0))
        .filter(VirtualCard.status != 'terminated').scalar() or 0.0
    )
    total_funded_usd = (
        db.session.query(db.func.coalesce(db.func.sum(CardTransaction.amount), 0.0))
        .filter(CardTransaction.type == 'funding').scalar() or 0.0
    )

    return {
        'total_cards':       total_cards,
        'active_cards':      active,
        'frozen_cards':      frozen,
        'terminated_cards':  terminated,
        'total_balance_usd': round(total_balance_usd, 2),
        'total_funded_usd':  round(total_funded_usd, 2),
    }
