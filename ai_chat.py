# ai_chat.py
#
# Backend for the in-app "AI Assistant" on the Support Center screen.
# Replaces the old WhatsApp redirect: instead of leaving the app, users
# now chat with an AI assistant that knows about every Cheap4U service
# (data, airtime, electricity, cable TV, exam pins, wallet funding,
# transfers, cashback, referrals, monthly challenges, rewards,
# transactions, login/password/verification issues, and general app
# usage). If the AI can't resolve the issue it tells the user to reach
# a human via the phone number / email shown on the Support screen.
#
# SECURITY NOTE: the OpenAI API key is read from the OPENAI_API_KEY
# environment variable (see conpig.py) and is NEVER sent to, or
# reachable from, the mobile app. The app only ever talks to our own
# /api/ai-chat/* endpoints over HTTPS; this file is the only place
# that talks to OpenAI.

import os
import uuid
import logging
from datetime import datetime, timedelta

from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity
from sqlalchemy import asc

from models import db, SupportChatMessage
from extensions import limiter

logger = logging.getLogger(__name__)

ai_chat_bp = Blueprint('ai_chat', __name__, url_prefix='/api/ai-chat')

# How many previous turns we feed back to the model as conversation
# context. Keeps token usage (and cost) predictable.
MAX_CONTEXT_MESSAGES = 12
MAX_MESSAGE_LENGTH = 1000

SYSTEM_PROMPT = """You are "Cheap4U Assistant", the friendly, fast, and professional
in-app AI support agent for Cheap4U Technology, a Nigerian VTU (Virtual Top-Up) app.

You help users with:
- Buying Data, Buying Airtime, Electricity Bills, Cable TV (DSTV/GOTV/Startimes/Showmax), Exam PIN (WAEC/NECO/NABTEB/JAMB)
- Wallet Funding, Transfers, Cashback, Referral Program, Monthly Challenges, Rewards
- Transactions, Failed Payments, Pending Transactions
- Login Problems, Password Reset, Account Verification
- General app usage and questions about Cheap4U Technology

Style rules:
- Be warm, concise, and confident. Prefer short paragraphs or a short numbered/bulleted list over long walls of text.
- Never invent transaction data, balances, or account details you were not given — you have no live access to the user's account.
- For anything involving a specific transaction, balance, or account action, guide the user on where in the app to look or what to check (e.g. "check the History tab"), rather than claiming a status you don't know.
- If the user's problem sounds like it needs a human (e.g. money debited but service not delivered, suspected fraud, account locked, or you're not fully sure how to resolve it), clearly tell them to contact human support using the phone number or email shown on the Support page, and keep your tone reassuring.
- Never ask the user for their password, PIN, OTP, or full card details. If asked, explain Cheap4U will never request these and you can't process them here.
- Keep replies focused — you're in a mobile chat bubble, not writing an essay.
"""


def _get_identity():
    """
    Returns (user_id, session_id).
    Works for both logged-in users (JWT) and guests (client-supplied
    session_id), so the AI assistant is usable even before login.
    """
    user_id = None
    try:
        verify_jwt_in_request(optional=True)
        identity = get_jwt_identity()
        if identity:
            user_id = int(identity)
    except Exception:
        user_id = None

    session_id = (request.json or {}).get('session_id') if request.is_json else None
    if not session_id:
        session_id = request.args.get('session_id')
    if not session_id:
        # Fall back to a per-user pseudo-session so logged-in users
        # always see the same history without the app having to
        # generate/store a session_id itself.
        session_id = f"user-{user_id}" if user_id else None

    return user_id, session_id


def _get_openai_client():
    api_key = current_app.config.get('OPENAI_API_KEY')
    if not api_key:
        return None
    from openai import OpenAI
    return OpenAI(api_key=api_key)


@ai_chat_bp.route('/message', methods=['POST'])
@limiter.limit("10 per minute")
def send_message():
    """
    Send a user message to the AI assistant and get a reply.
    Body: { "message": "...", "session_id": "optional-guest-session-id" }
    """
    data = request.get_json(silent=True) or {}
    message = (data.get('message') or '').strip()

    if not message:
        return jsonify({'status': 'error', 'message': 'Message cannot be empty'}), 400
    if len(message) > MAX_MESSAGE_LENGTH:
        return jsonify({
            'status': 'error',
            'message': f'Message is too long (max {MAX_MESSAGE_LENGTH} characters)'
        }), 400

    user_id, session_id = _get_identity()
    if not session_id:
        session_id = str(uuid.uuid4())

    try:
        client = _get_openai_client()
        if client is None:
            logger.error('OPENAI_API_KEY not configured')
            return jsonify({
                'status': 'error',
                'message': (
                    "Our AI assistant is temporarily unavailable. "
                    "Please contact support using the phone number or email on this page."
                )
            }), 503

        # Pull recent history for conversation context
        history = (
            SupportChatMessage.query
            .filter_by(session_id=session_id)
            .order_by(asc(SupportChatMessage.created_at))
            .all()
        )[-MAX_CONTEXT_MESSAGES:]

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for m in history:
            messages.append({"role": m.role, "content": m.content})
        messages.append({"role": "user", "content": message})

        model = current_app.config.get('OPENAI_MODEL', 'gpt-4o-mini')

        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=500,
            temperature=0.6,
        )
        reply = response.choices[0].message.content.strip()

    except Exception as e:
        logger.error(f'AI chat error: {e}')
        return jsonify({
            'status': 'error',
            'message': (
                "Sorry, I couldn't process that right now. "
                "Please try again, or contact support using the phone number or email on this page."
            )
        }), 502

    # Persist both sides of the conversation
    try:
        db.session.add(SupportChatMessage(
            user_id=user_id, session_id=session_id, role='user', content=message
        ))
        db.session.add(SupportChatMessage(
            user_id=user_id, session_id=session_id, role='assistant', content=reply
        ))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.warning(f'Could not save chat history (non-fatal): {e}')

    return jsonify({
        'status': 'success',
        'reply': reply,
        'session_id': session_id,
    })


@ai_chat_bp.route('/history', methods=['GET'])
@limiter.limit("30 per minute")
def get_history():
    """Return this user's/guest session's chat history, oldest first."""
    user_id, session_id = _get_identity()
    if not session_id:
        return jsonify({'status': 'success', 'data': []})

    messages = (
        SupportChatMessage.query
        .filter_by(session_id=session_id)
        .order_by(asc(SupportChatMessage.created_at))
        .limit(200)
        .all()
    )
    return jsonify({
        'status': 'success',
        'session_id': session_id,
        'data': [m.to_dict() for m in messages],
    })


@ai_chat_bp.route('/history', methods=['DELETE'])
@limiter.limit("10 per minute")
def clear_history():
    """Clear this user's/guest session's chat history (e.g. 'New chat')."""
    user_id, session_id = _get_identity()
    if not session_id:
        return jsonify({'status': 'success', 'message': 'Nothing to clear'})

    try:
        SupportChatMessage.query.filter_by(session_id=session_id).delete()
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.warning(f'Could not clear chat history: {e}')
        return jsonify({'status': 'error', 'message': 'Could not clear history'}), 500

    return jsonify({'status': 'success', 'message': 'Chat history cleared'})


@ai_chat_bp.route('/contact-info', methods=['GET'])
def contact_info():
    """
    Lets the app fetch the support phone/email/hours from the backend
    instead of hardcoding them, so they can be updated without an app
    release. Optional — the app also ships with local defaults.
    """
    return jsonify({
        'status': 'success',
        'data': {
            'phone': current_app.config.get('SUPPORT_PHONE', '+2349037663816'),
            'email': current_app.config.get('SUPPORT_EMAIL', 'support@cheap4utechnology.com'),
            'business_hours': 'Monday - Sunday, 24 Hours Support',
        }
    })
