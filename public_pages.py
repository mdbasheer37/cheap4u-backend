# public_pages.py
#
# Public-facing HTML pages for Cheap4U:
#   GET  /delete-account        — Google Play "Delete Account" requirement
#   POST /delete-account        — saves the deletion request
#   GET  /privacy-policy        — same text shown in-app, as a public URL
#   GET  /terms-of-service      — same text shown in-app, as a public URL
#
# These are plain server-rendered HTML pages (no JavaScript required), so
# they work correctly when opened directly in a browser — including by
# the Google Play review team — and when deployed on Render.

import re
import logging
from datetime import datetime

from flask import Blueprint, render_template, request, jsonify, current_app

from models import db, AccountDeletionRequest
from extensions import limiter
from utils import validate_email

logger = logging.getLogger(__name__)

public_pages_bp = Blueprint('public_pages', __name__)

# Loose international/local phone check: optional leading +, 10-14 digits.
_PHONE_RE = re.compile(r'^\+?\d{10,14}$')

# Minimum seconds between the page loading and the form being submitted.
# Bots that fetch the page and POST immediately get rejected; a real
# person filling in four fields will always take longer than this.
_MIN_SUBMIT_SECONDS = 3


def _clean_phone(raw):
    return re.sub(r'[\s\-()]', '', (raw or '').strip())


# ─────────────────────────────────────────────────────────────────────
# Delete Account
# ─────────────────────────────────────────────────────────────────────

@public_pages_bp.route('/delete-account', methods=['GET'])
def delete_account_page():
    return render_template(
        'delete_account.html',
        support_email=current_app.config.get('SUPPORT_EMAIL'),
        support_phone=current_app.config.get('SUPPORT_PHONE'),
        form_loaded_at=datetime.utcnow().timestamp(),
        submitted=False,
    )


@public_pages_bp.route('/delete-account', methods=['POST'])
@limiter.limit('5 per hour')
def delete_account_submit():
    wants_json = request.is_json
    data = request.get_json(silent=True) if wants_json else request.form

    def respond(success, message, status=200, form_values=None):
        if wants_json:
            return jsonify({'success': success, 'message': message}), status
        return render_template(
            'delete_account.html',
            support_email=current_app.config.get('SUPPORT_EMAIL'),
            support_phone=current_app.config.get('SUPPORT_PHONE'),
            form_loaded_at=datetime.utcnow().timestamp(),
            submitted=True,
            success=success,
            message=message,
            form_values=form_values or {},
        ), status

    # ── Spam protection 1: honeypot field ──────────────────────────
    # Hidden from real users via CSS; only bots that auto-fill every
    # field on the page will ever populate this. Reply as if it worked
    # so scripted bots don't learn to detect and skip the field.
    if (data.get('website') or '').strip():
        logger.warning(f'Delete-account honeypot triggered from {request.remote_addr}')
        return respond(True, 'Your request has been received. We will contact you shortly.')

    # ── Spam protection 2: minimum time-on-page ────────────────────
    try:
        loaded_at = float(data.get('form_loaded_at', 0))
    except (TypeError, ValueError):
        loaded_at = 0
    if loaded_at and (datetime.utcnow().timestamp() - loaded_at) < _MIN_SUBMIT_SECONDS:
        logger.warning(f'Delete-account submitted too fast from {request.remote_addr}')
        return respond(False, 'Please take a moment to fill in the form and try again.', status=400)

    full_name = (data.get('full_name') or '').strip()
    email     = (data.get('email') or '').strip().lower()
    phone     = _clean_phone(data.get('phone'))
    reason    = (data.get('reason') or '').strip()

    form_values = {'full_name': full_name, 'email': email, 'phone': phone, 'reason': reason}

    # ── Input validation ────────────────────────────────────────────
    errors = []
    if not full_name or len(full_name) < 2:
        errors.append('Please enter your full name.')
    elif len(full_name) > 100:
        errors.append('Full name is too long.')

    if not email or not validate_email(email):
        errors.append('Please enter a valid email address.')
    elif len(email) > 100:
        errors.append('Email address is too long.')

    if not phone or not _PHONE_RE.match(phone):
        errors.append('Please enter a valid phone number.')

    if reason and len(reason) > 1000:
        errors.append('Reason is too long (max 1000 characters).')

    if errors:
        return respond(False, ' '.join(errors), status=400, form_values=form_values)

    try:
        record = AccountDeletionRequest(
            full_name=full_name[:100],
            email=email[:100],
            phone=phone[:20],
            reason=reason[:1000] if reason else None,
            ip_address=(request.headers.get('X-Forwarded-For', request.remote_addr) or '')[:100].split(',')[0].strip(),
            user_agent=(request.headers.get('User-Agent') or '')[:255],
        )
        db.session.add(record)
        db.session.commit()
        logger.info(f'🗑️ Account deletion request #{record.id} received from {email} ({phone})')
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to save account deletion request: {e}')
        return respond(
            False,
            f"Something went wrong saving your request. Please email us directly at "
            f"{current_app.config.get('SUPPORT_EMAIL')}.",
            status=500,
            form_values=form_values,
        )

    return respond(
        True,
        'Your account deletion request has been received. Our team will process it '
        'and contact you by email or phone if we need anything else.',
    )


# ─────────────────────────────────────────────────────────────────────
# Privacy Policy / Terms of Service — public HTML versions of the same
# text shown inside the app, so links (e.g. in the Play Store listing,
# or the footer of the Delete Account page) have somewhere to go.
# ─────────────────────────────────────────────────────────────────────

TERMS_OF_SERVICE_TEXT = """Terms of Service

Last updated: July 2026

Welcome to Cheap4U. These Terms of Service ("Terms") govern your use of the Cheap4U mobile application ("App") operated by Cheap4U Technology ("we", "us", "our"). By creating an account or using the App, you agree to these Terms. If you do not agree, please do not use the App.

1. Our Services
Cheap4U lets you purchase airtime, mobile data, cable TV subscriptions, electricity tokens, and examination PINs (WAEC, NECO, NABTEB, JAMB), and to fund and manage an in-app wallet, for yourself or on behalf of another phone number/meter/smartcard that you provide.

2. Account Registration
You must provide accurate, current information when creating an account, including a valid phone number and email address. You are responsible for keeping your login details and OTPs confidential. You must be at least 18 years old, or have the consent of a parent/guardian, to use this App.

3. Wallet & Payments
- Wallet funding is processed through Paystack. We do not store your card details.
- Prices shown in the App include our service margin and may change without prior notice to reflect changes in provider pricing.
- All wallet top-ups and successful purchases are final. We do not offer cash refunds for wallet balance; unused balance remains in your wallet for future purchases.

4. Accuracy of Recipient Details
You are solely responsible for entering the correct phone number, meter number, smartcard number, or exam type before confirming any purchase. Once a transaction is successfully processed by the network/provider, we cannot reverse, cancel, or refund it because of an incorrect number you supplied. Please double-check all details before confirming.

5. Service Availability
Airtime, data, cable, and electricity purchases depend on third-party network providers (MTN, Airtel, Glo, 9Mobile, DSTV, GOTV, StarTimes, and electricity distribution companies) and payment processors. We are not responsible for delays, failures, or downtime caused by these third parties, but we will make reasonable efforts to reverse your wallet debit if a provider confirms a transaction genuinely failed on their end.

6. Referral Program
Referral bonuses are credited according to the terms displayed in the Referral section of the App at the time you earn them. We reserve the right to withhold or reverse referral bonuses obtained through fraud, fake accounts, or abuse of the program, and to change referral terms for future referrals at any time.

7. Prohibited Use
You agree not to use the App for money laundering, fraud, purchasing services for resale without our written permission, or any illegal activity. We may suspend or terminate accounts that violate this section, misuse the referral program, or attempt to abuse wallet funding (e.g. chargebacks after successful purchases).

8. Limitation of Liability
To the maximum extent permitted by law, Cheap4U Technology is not liable for indirect, incidental, or consequential damages arising from your use of the App, including losses from an incorrectly entered recipient number, network provider downtime, or unauthorized access to your account caused by your failure to keep your login details secure.

9. Changes to These Terms
We may update these Terms from time to time. Continued use of the App after changes are posted means you accept the updated Terms.

10. Governing Law
These Terms are governed by the laws of the Federal Republic of Nigeria.

11. Contact Us
For questions about these Terms, contact us through the support option in the App."""

PRIVACY_POLICY_TEXT = """Privacy Policy

Last updated: July 2026

This Privacy Policy explains how Cheap4U Technology collects, uses, and protects your information when you use the Cheap4U mobile application ("App").

1. Information We Collect
- Account information: your name, email address, phone number, and password (stored securely as a hash, never in plain text).
- Transaction information: purchases you make (airtime, data, cable TV, electricity, exam pins), amounts, timestamps, and recipient details you enter (phone numbers, meter numbers, smartcard numbers).
- Wallet & payment information: wallet balance and funding history. Card/bank details you enter to fund your wallet are handled directly by Paystack, our payment processor - we do not receive or store your full card number, CVV, or PIN.
- Device & usage information: basic technical information such as app version and error logs, used to diagnose and fix problems.

2. How We Use Your Information
- To process your airtime, data, cable TV, electricity, and exam PIN purchases through our provider partners (CheapDataHub, VTpass, and similar VTU providers).
- To fund and manage your in-app wallet, including via Paystack.
- To communicate with you about your transactions, account, or referral earnings (via SMS, email, or in-app notifications).
- To detect and prevent fraud, and to enforce our Terms of Service.
- To improve the App and fix bugs.

3. How We Share Your Information
We share only what's necessary to provide the service:
- With Paystack, to process wallet funding.
- With our VTU provider partners (e.g. CheapDataHub, VTpass), to fulfil the specific airtime/data/cable/electricity/exam-pin purchase you request - this includes the recipient phone number, meter number, or smartcard number you provide.
- With SMS/communication providers, to send you OTPs and transaction notifications.
- We do not sell your personal information to advertisers or other third parties.
- We may disclose information if required by Nigerian law or a valid legal request.

4. Data Retention
We retain your account and transaction data for as long as your account is active, and for a reasonable period afterward as required for accounting, fraud prevention, and legal compliance.

5. Your Rights
You can review and update your profile information in the App at any time. You may request account deletion through the Account Deletion option in your Profile, or via the public Delete Account page at /delete-account - this will deactivate your account and remove your personal data from our active systems, except where we are required to retain transaction records for legal/accounting purposes.

6. Security
We use industry-standard measures (password hashing, encrypted connections, rate limiting) to protect your data. No method of transmission or storage is 100% secure, but we work to protect your information to the best of our ability.

7. Children's Privacy
The App is not directed at children under 18. We do not knowingly collect data from children under 18.

8. Changes to This Policy
We may update this Privacy Policy from time to time. We will indicate the "Last updated" date above when changes are made. Continued use of the App after changes are posted means you accept the updated Policy.

9. Contact Us
For questions about this Privacy Policy or your data, contact us through the support option in the App."""


def _parse_legal_sections(raw_text):
    """Splits one of the legal text blocks above into a list of
    {heading, text} dicts for the template to render. The first two
    blank-line-separated blocks (title, 'Last updated: ...') are
    skipped since the template already renders those separately."""
    blocks = [b.strip('\n') for b in raw_text.strip().split('\n\n')]
    sections = []
    for block in blocks[2:]:
        lines = block.split('\n')
        heading_match = re.match(r'^\d+\.\s+.+$', lines[0])
        if heading_match:
            heading = lines[0]
            body = '\n'.join(lines[1:]).strip()
        else:
            heading = None
            body = block
        sections.append({'heading': heading, 'text': body})
    return sections


@public_pages_bp.route('/privacy-policy', methods=['GET'])
def privacy_policy_page():
    return render_template(
        'privacy_policy.html',
        sections=_parse_legal_sections(PRIVACY_POLICY_TEXT),
        support_email=current_app.config.get('SUPPORT_EMAIL'),
    )


@public_pages_bp.route('/terms-of-service', methods=['GET'])
def terms_of_service_page():
    return render_template(
        'terms_of_service.html',
        sections=_parse_legal_sections(TERMS_OF_SERVICE_TEXT),
        support_email=current_app.config.get('SUPPORT_EMAIL'),
    )
