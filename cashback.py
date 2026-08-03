# cashback.py — Cashback System: core business logic
#
# Self-contained, following the same shape as challenge.py, so it can be
# dropped into the existing backend without touching models.py. Imported by:
#   - cheapdatahub.py / vtunaija.py   → award_cashback()  (right after each
#                                        successful purchase, alongside the
#                                        existing record_purchase() call)
#   - cashback_routes.py              → everything else
#   - app.py                          → start_scheduler()

import logging
from datetime import datetime, timedelta

from models import db, User, Transaction
from cashback_models import CashbackConfig, CashbackWallet, CashbackEntry

logger = logging.getLogger(__name__)

# Transaction.type → CashbackConfig percent field. Deliberately a whitelist
# (not "everything that isn't excluded") so a new internal transaction type
# (wallet_funding, withdrawal, airtime_to_cash, referral payouts, the
# cashback redemption itself, etc.) never accidentally earns cashback.
CATEGORY_PERCENT_FIELD = {
    'airtime':     'percent_airtime',
    'data':        'percent_data',
    'electricity': 'percent_electricity',
    'cable_tv':    'percent_cable_tv',
    'exam_pin':    'percent_exam_pin',
}

_scheduler_started = False


# ── config / wallet helpers ────────────────────────────────────────────────

def get_config():
    cfg = CashbackConfig.query.get(1)
    if not cfg:
        cfg = CashbackConfig(id=1)
        db.session.add(cfg)
        db.session.flush()
    return cfg


def get_or_create_wallet(user_id):
    wallet = CashbackWallet.query.filter_by(user_id=user_id).first()
    if not wallet:
        wallet = CashbackWallet(user_id=user_id, balance=0.0, total_earned=0.0,
                                 total_redeemed=0.0, total_expired=0.0)
        db.session.add(wallet)
        db.session.flush()
    return wallet


# ── earning cashback (the hook) ────────────────────────────────────────────

def award_cashback(transaction):
    """
    Call this once a Transaction has been set to status='success', BEFORE
    the caller's final db.session.commit() — exactly like challenge.py's
    record_purchase(). It only adds/updates rows in the current session
    (no commit here) so it rides along with the same atomic commit as the
    purchase itself.

    Safe to call unconditionally: it no-ops for non-qualifying transaction
    types or amounts, and any unexpected error is swallowed and logged — a
    cashback bug must never block or roll back a real, paid-for purchase.
    """
    try:
        if not transaction or transaction.type not in CATEGORY_PERCENT_FIELD:
            return
        if not transaction.amount or transaction.amount <= 0:
            return

        cfg = get_config()
        if not cfg.is_enabled:
            return
        if transaction.amount < (cfg.min_transaction_amount or 0):
            return

        percent = getattr(cfg, CATEGORY_PERCENT_FIELD[transaction.type], 0) or 0
        if percent <= 0:
            return

        cashback_amount = round(transaction.amount * percent / 100.0, 2)
        if cfg.max_cashback_per_transaction:
            cashback_amount = min(cashback_amount, cfg.max_cashback_per_transaction)
        if cashback_amount <= 0:
            return

        wallet = get_or_create_wallet(transaction.user_id)
        wallet.balance      = round((wallet.balance or 0) + cashback_amount, 2)
        wallet.total_earned = round((wallet.total_earned or 0) + cashback_amount, 2)

        expires_at = (
            datetime.utcnow() + timedelta(days=cfg.expiry_days)
            if cfg.expiry_days else None
        )

        db.session.add(CashbackEntry(
            user_id          = transaction.user_id,
            transaction_id   = transaction.id,
            type             = 'earned',
            category         = transaction.type,
            amount           = cashback_amount,
            remaining_amount = cashback_amount,
            balance_after    = wallet.balance,
            source_amount    = transaction.amount,
            percent_applied  = percent,
            expires_at       = expires_at,
            note             = f'Cashback on {transaction.type} purchase',
        ))
        logger.info(f'[Cashback] +₦{cashback_amount:,.2f} for user {transaction.user_id} '
                    f'({transaction.type}, {percent}%)')
    except Exception:
        logger.exception('[Cashback] award_cashback failed (non-fatal, purchase unaffected)')


# ── read helpers ────────────────────────────────────────────────────────────

def get_wallet_summary(user_id):
    wallet = get_or_create_wallet(user_id)
    cfg = get_config()

    soon = datetime.utcnow() + timedelta(days=7)
    expiring_soon = (
        db.session.query(db.func.coalesce(db.func.sum(CashbackEntry.remaining_amount), 0.0))
        .filter(
            CashbackEntry.user_id == user_id,
            CashbackEntry.type.in_(('earned', 'admin_credit')),
            CashbackEntry.is_expired.is_(False),
            CashbackEntry.remaining_amount > 0,
            CashbackEntry.expires_at.isnot(None),
            CashbackEntry.expires_at <= soon,
        )
        .scalar()
    ) or 0.0

    next_lot = (
        CashbackEntry.query
        .filter(
            CashbackEntry.user_id == user_id,
            CashbackEntry.type.in_(('earned', 'admin_credit')),
            CashbackEntry.is_expired.is_(False),
            CashbackEntry.remaining_amount > 0,
            CashbackEntry.expires_at.isnot(None),
        )
        .order_by(CashbackEntry.expires_at.asc())
        .first()
    )

    data = wallet.to_dict()
    data.update({
        'cashback_enabled':    cfg.is_enabled,
        'expiring_soon':       round(expiring_soon, 2),
        'next_expiry_date':    next_lot.expires_at.isoformat() if next_lot else None,
        'min_redeem_amount':   cfg.min_redeem_amount,
        'can_redeem':          wallet.balance >= cfg.min_redeem_amount and wallet.balance > 0,
    })
    return data


def get_rates():
    cfg = get_config()
    return {
        'cashback_enabled':        cfg.is_enabled,
        'rates': {
            'airtime':     cfg.percent_airtime,
            'data':        cfg.percent_data,
            'electricity': cfg.percent_electricity,
            'cable_tv':    cfg.percent_cable_tv,
            'exam_pin':    cfg.percent_exam_pin,
        },
        'min_transaction_amount':       cfg.min_transaction_amount,
        'max_cashback_per_transaction': cfg.max_cashback_per_transaction,
        'expiry_days':                  cfg.expiry_days,
    }


def get_history(user_id, page=1, per_page=30):
    per_page = min(per_page, 100)
    q = CashbackEntry.query.filter_by(user_id=user_id).order_by(CashbackEntry.created_at.desc())
    total = q.count()
    rows = q.limit(per_page).offset((page - 1) * per_page).all()
    pages = (total + per_page - 1) // per_page if per_page else 1
    return [r.to_dict() for r in rows], total, pages


# ── redeeming ───────────────────────────────────────────────────────────────

def redeem(user_id, amount=None):
    """
    Move cashback balance into the user's main spendable wallet. If `amount`
    is None, redeems the entire current balance. Consumes the oldest earned
    lots first (FIFO) so remaining_amount stays accurate for expiry.
    """
    wallet = get_or_create_wallet(user_id)
    cfg = get_config()

    if wallet.balance <= 0:
        return {'status': 'error', 'message': 'No cashback balance available to redeem'}

    if amount is None:
        amount = wallet.balance
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        return {'status': 'error', 'message': 'Invalid amount'}

    if amount <= 0:
        return {'status': 'error', 'message': 'Amount must be greater than zero'}
    if amount > wallet.balance:
        return {'status': 'error', 'message': f'Amount exceeds available cashback balance (₦{wallet.balance:,.2f})'}
    if amount < cfg.min_redeem_amount and amount < wallet.balance:
        return {'status': 'error',
                'message': f'Minimum redeem amount is ₦{cfg.min_redeem_amount:,.2f} '
                           f'(or redeem your full balance)'}

    user = User.query.get(user_id)
    if not user:
        return {'status': 'error', 'message': 'User not found'}

    # Draw down oldest unexpired lots first
    remaining_to_consume = amount
    lots = (
        CashbackEntry.query
        .filter(
            CashbackEntry.user_id == user_id,
            CashbackEntry.type.in_(('earned', 'admin_credit')),
            CashbackEntry.is_expired.is_(False),
            CashbackEntry.remaining_amount > 0,
        )
        .order_by(CashbackEntry.created_at.asc())
        .all()
    )
    for lot in lots:
        if remaining_to_consume <= 0:
            break
        draw = min(lot.remaining_amount, remaining_to_consume)
        lot.remaining_amount = round(lot.remaining_amount - draw, 2)
        remaining_to_consume = round(remaining_to_consume - draw, 2)

    user.wallet_balance = round(user.wallet_balance + amount, 2)
    wallet.balance         = round(wallet.balance - amount, 2)
    wallet.total_redeemed  = round(wallet.total_redeemed + amount, 2)

    db.session.add(CashbackEntry(
        user_id       = user_id,
        type          = 'redeemed',
        amount        = amount,
        balance_after = wallet.balance,
        note          = 'Redeemed to main wallet',
    ))
    db.session.commit()

    return {
        'status':  'success',
        'message': f'₦{amount:,.2f} moved from cashback to your main wallet',
        'data': {
            'amount':               amount,
            'cashback_balance':     round(wallet.balance, 2),
            'wallet_balance':       round(user.wallet_balance, 2),
        },
    }


# ── admin ────────────────────────────────────────────────────────────────────

def admin_adjust(user_id, amount, note=None):
    """Admin manually credits or debits a user's cashback balance.
    Positive amount = credit (never expires unless a later feature adds it),
    negative amount = debit (drawn from oldest lots first, like a redeem)."""
    user = User.query.get(user_id)
    if not user:
        return {'status': 'error', 'message': 'User not found'}
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        return {'status': 'error', 'message': 'Invalid amount'}
    if amount == 0:
        return {'status': 'error', 'message': 'Amount cannot be zero'}

    wallet = get_or_create_wallet(user_id)

    if amount > 0:
        wallet.balance      = round(wallet.balance + amount, 2)
        wallet.total_earned = round(wallet.total_earned + amount, 2)
        db.session.add(CashbackEntry(
            user_id=user_id, type='admin_credit', amount=amount,
            remaining_amount=amount, balance_after=wallet.balance,
            note=note or 'Admin credit',
        ))
    else:
        debit = min(abs(amount), wallet.balance)
        if debit <= 0:
            return {'status': 'error', 'message': 'User has no cashback balance to debit'}
        remaining_to_consume = debit
        lots = (
            CashbackEntry.query
            .filter(
                CashbackEntry.user_id == user_id,
                CashbackEntry.type.in_(('earned', 'admin_credit')),
                CashbackEntry.is_expired.is_(False),
                CashbackEntry.remaining_amount > 0,
            )
            .order_by(CashbackEntry.created_at.asc())
            .all()
        )
        for lot in lots:
            if remaining_to_consume <= 0:
                break
            draw = min(lot.remaining_amount, remaining_to_consume)
            lot.remaining_amount = round(lot.remaining_amount - draw, 2)
            remaining_to_consume = round(remaining_to_consume - draw, 2)
        wallet.balance = round(wallet.balance - debit, 2)
        db.session.add(CashbackEntry(
            user_id=user_id, type='admin_debit', amount=debit,
            balance_after=wallet.balance, note=note or 'Admin debit',
        ))

    db.session.commit()
    return {'status': 'success', 'message': 'Cashback balance adjusted', 'data': wallet.to_dict()}


def get_platform_stats():
    totals = db.session.query(
        db.func.coalesce(db.func.sum(CashbackWallet.balance), 0.0),
        db.func.coalesce(db.func.sum(CashbackWallet.total_earned), 0.0),
        db.func.coalesce(db.func.sum(CashbackWallet.total_redeemed), 0.0),
        db.func.coalesce(db.func.sum(CashbackWallet.total_expired), 0.0),
        db.func.count(CashbackWallet.id),
    ).first()

    outstanding, earned, redeemed, expired, wallet_count = totals

    top_earners = (
        db.session.query(CashbackWallet, User)
        .join(User, User.id == CashbackWallet.user_id)
        .order_by(CashbackWallet.total_earned.desc())
        .limit(10)
        .all()
    )

    return {
        'total_wallets':            wallet_count or 0,
        'outstanding_balance':      round(outstanding or 0, 2),
        'total_earned_all_time':    round(earned or 0, 2),
        'total_redeemed_all_time':  round(redeemed or 0, 2),
        'total_expired_all_time':   round(expired or 0, 2),
        'top_earners': [
            {
                'user_id':      u.id,
                'name':         u.name,
                'email':        u.email,
                'balance':      round(w.balance, 2),
                'total_earned': round(w.total_earned, 2),
            }
            for w, u in top_earners
        ],
    }


def list_wallets(search=None, page=1, per_page=30):
    per_page = min(per_page, 100)
    q = db.session.query(CashbackWallet, User).join(User, User.id == CashbackWallet.user_id)
    if search:
        like = f'%{search}%'
        q = q.filter(db.or_(User.name.ilike(like), User.email.ilike(like), User.phone.ilike(like)))
    q = q.order_by(CashbackWallet.balance.desc())
    total = q.count()
    rows = q.limit(per_page).offset((page - 1) * per_page).all()
    pages = (total + per_page - 1) // per_page if per_page else 1
    return [
        {
            'user_id':        u.id,
            'name':           u.name,
            'email':          u.email,
            'phone':          u.phone,
            **w.to_dict(),
        }
        for w, u in rows
    ], total, pages


# ── expiry sweep ─────────────────────────────────────────────────────────────

def expire_due_entries():
    """
    Finds every earned/admin_credit lot whose expiry date has passed and
    still has an unconsumed remaining_amount, deducts that amount from the
    owner's wallet balance, and logs an 'expired' ledger entry. Idempotent —
    each lot is flagged is_expired once processed so re-running is safe.
    """
    now = datetime.utcnow()
    due = (
        CashbackEntry.query
        .filter(
            CashbackEntry.type.in_(('earned', 'admin_credit')),
            CashbackEntry.is_expired.is_(False),
            CashbackEntry.remaining_amount > 0,
            CashbackEntry.expires_at.isnot(None),
            CashbackEntry.expires_at <= now,
        )
        .all()
    )
    if not due:
        return {'status': 'success', 'expired_count': 0, 'expired_amount': 0.0}

    total_expired = 0.0
    for lot in due:
        amount_expired = lot.remaining_amount
        wallet = get_or_create_wallet(lot.user_id)
        wallet.balance        = round(max(0.0, wallet.balance - amount_expired), 2)
        wallet.total_expired  = round(wallet.total_expired + amount_expired, 2)

        lot.remaining_amount = 0.0
        lot.is_expired = True

        db.session.add(CashbackEntry(
            user_id=lot.user_id, type='expired', category=lot.category,
            amount=amount_expired, balance_after=wallet.balance,
            note=f'Cashback lot #{lot.id} expired',
        ))
        total_expired += amount_expired

    db.session.commit()
    logger.info(f'[Cashback] Expired {len(due)} lot(s), total ₦{total_expired:,.2f}')
    return {'status': 'success', 'expired_count': len(due), 'expired_amount': round(total_expired, 2)}


# ── scheduler ────────────────────────────────────────────────────────────────

def start_scheduler(app):
    """
    Starts a lightweight background job that sweeps for expired cashback
    lots every hour. Call once from app.py after create_app(), mirroring
    challenge.py's start_scheduler().
    """
    global _scheduler_started
    if _scheduler_started:
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        logger.warning('APScheduler not installed — cashback auto-expiry disabled. '
                        'Add "APScheduler" to requirements.txt.')
        return

    def _job():
        with app.app_context():
            try:
                result = expire_due_entries()
                if result.get('expired_count'):
                    logger.info(f'[Cashback] Scheduled expiry sweep: {result}')
            except Exception:
                logger.exception('[Cashback] scheduled expiry sweep failed')

    scheduler = BackgroundScheduler(daemon=True, timezone='UTC')
    scheduler.add_job(_job, 'interval', hours=1, next_run_time=datetime.utcnow() + timedelta(seconds=45))
    scheduler.start()
    _scheduler_started = True
