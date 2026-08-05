# ai_context.py — Smart AI Assistant: live account grounding
#
# The AI Assistant (ai_chat.py) previously had zero access to real account
# data and was explicitly told never to invent it — safe, but it meant
# every "why did my payment fail?" or "what's my balance?" question got
# deflected to "check the app" instead of actually being answered.
#
# This module builds a compact, factual text block from the user's own
# real data — wallet balance, recent transactions (with the actual stored
# failure reason for failed ones), and real data-plan recommendations
# pulled from the Smart Price Comparison engine — that gets injected into
# the Gemini system prompt for that one request. The model still can't
# invent anything: it either has real data here, or it doesn't and says so.

import re
from models import User, Transaction

_DATA_PLAN_KEYWORDS = (
    'data plan', 'best data', 'recommend', 'which data', 'cheap data',
    'cheapest data', 'good data plan', 'best plan', 'which plan',
)

_FAILED_PAYMENT_KEYWORDS = (
    'failed', 'declined', "didn't go through", "didn't receive", 'not delivered',
    'pending', 'stuck', 'not credited', 'reversed', 'refund',
)


def _looks_like_data_plan_question(message):
    t = (message or '').lower()
    return any(k in t for k in _DATA_PLAN_KEYWORDS)


def _looks_like_transaction_question(message):
    t = (message or '').lower()
    return any(k in t for k in _FAILED_PAYMENT_KEYWORDS) or 'transaction' in t or 'reference' in t


# Public aliases — ai_chat.py uses these to decide cache eligibility
# without reaching into underscore-prefixed internals.
is_data_plan_question = _looks_like_data_plan_question
is_transaction_question = _looks_like_transaction_question


def build_account_context(user_id, message):
    """Returns a compact text block for one request, or '' if there's no
    logged-in user to ground against."""
    if not user_id:
        return ""

    user = User.query.get(user_id)
    if not user:
        return ""

    lines = [f"Account holder: {user.name or 'the user'}. Wallet balance: ₦{user.wallet_balance:,.2f}."]

    recent = (
        Transaction.query.filter_by(user_id=user_id)
        .order_by(Transaction.created_at.desc())
        .limit(6)
        .all()
    )

    if not recent:
        lines.append("This user has made no transactions yet — they are brand new to Cheap4U. "
                      "Prioritize a friendly, simple walkthrough of how to buy their first airtime/data.")
    elif _looks_like_transaction_question(message):
        lines.append("Recent transactions (most recent first):")
        for t in recent:
            when = t.created_at.strftime('%b %d, %I:%M %p') if t.created_at else 'unknown time'
            detail = ""
            if t.status == 'failed':
                err = (t.details or {}).get('error')
                detail = f" — recorded reason: {err}" if err else " — no specific reason recorded"
            lines.append(f"- {when}: {t.type} for ₦{t.amount:,.2f}, status: {t.status}{detail} (ref {t.reference})")
    else:
        # Lighter touch when the question isn't specifically about transactions
        last = recent[0]
        lines.append(f"Their most recent transaction was a {last.type} of ₦{last.amount:,.2f} ({last.status}).")

    if _looks_like_data_plan_question(message):
        try:
            import price_comparison
            comparison = price_comparison.compare_data_plans()
            top_plans = comparison.get('plans', [])[:5]
            if top_plans:
                lines.append("Top real data plans available right now, ranked by overall value "
                              "(price + delivery speed + reliability):")
                for p in top_plans:
                    lines.append(
                        f"- {p['provider']} {p['size']} ({p['plan_type']}) — ₦{p['price']:,.2f}, "
                        f"value score {p['value_score']}/100"
                    )
        except Exception:
            pass  # comparison engine is a nice-to-have here, never block the chat reply on it

    return "\n".join(lines)
