# card_provider.py — Virtual Dollar Card: pluggable issuer abstraction
#
# ─────────────────────────────────────────────────────────────────────────
# WHY THIS FILE EXISTS
# ─────────────────────────────────────────────────────────────────────────
# Issuing real virtual dollar cards requires a licensed card-issuing
# provider (e.g. Sudo Africa, Union54, Flutterwave Virtual Cards, Bridgecard)
# — a paid, KYC-gated commercial integration this project doesn't have
# credentials for. Building the whole feature against ONE specific
# provider's SDK would mean rewriting card.py, card_routes.py and the
# Kivy screen every time you switch providers or add a second one.
#
# Instead, every provider (real or mock) implements the same CardProvider
# interface below. card.py and the API/UI only ever talk to that interface.
# To go live with a real provider:
#   1. Create a class implementing CardProvider (e.g. SudoCardProvider).
#   2. Register it in PROVIDERS at the bottom of this file.
#   3. Set CardConfig.provider_name to its key (admin panel → Card Config).
# Nothing else changes — not card.py, not the routes, not the Kivy screen.
# ─────────────────────────────────────────────────────────────────────────

import random
import string
from abc import ABC, abstractmethod
from datetime import datetime


class CardProviderError(Exception):
    """Raised by a provider implementation when the issuer rejects an
    operation (declined KYC, insufficient issuer float, card not found,
    etc). card.py catches this and turns it into a clean API error —
    it never leaks provider-specific exceptions upward."""
    pass


class CardProvider(ABC):
    """The contract every card issuer integration must satisfy."""

    name = 'base'

    @abstractmethod
    def create_card(self, user, funding_amount_usd):
        """Issue a new card funded with `funding_amount_usd`.
        Returns a dict: {provider_card_id, card_number_masked, card_brand,
        expiry_month, expiry_year, cardholder_name, balance}."""
        raise NotImplementedError

    @abstractmethod
    def fund_card(self, provider_card_id, amount_usd):
        """Add funds to an existing card. Returns the new balance (float)."""
        raise NotImplementedError

    @abstractmethod
    def freeze_card(self, provider_card_id):
        """Suspends the card from further spend. Returns True on success."""
        raise NotImplementedError

    @abstractmethod
    def unfreeze_card(self, provider_card_id):
        """Re-activates a frozen card. Returns True on success."""
        raise NotImplementedError

    @abstractmethod
    def terminate_card(self, provider_card_id):
        """Permanently closes the card. Returns the final withdrawable
        balance (float) so the caller can refund it to the user's wallet."""
        raise NotImplementedError

    @abstractmethod
    def get_balance(self, provider_card_id):
        """Returns the authoritative current balance (float) from the
        issuer, used to reconcile our local copy."""
        raise NotImplementedError

    @abstractmethod
    def get_transactions(self, provider_card_id):
        """Returns a list of dicts: {type, amount, description, created_at}
        pulled from the issuer's own record (e.g. card purchases made
        outside our app, at a merchant). Optional to fully implement —
        our own funding/withdrawal events are always tracked locally
        regardless of what this returns."""
        raise NotImplementedError


class MockCardProvider(CardProvider):
    """
    Development/demo provider — no real money, no real card network. Lets
    the whole feature (creation, funding, freezing, deletion, balances,
    admin monitoring) work end-to-end today so the UI and business logic
    can be built and tested, and swapped for a real issuer later with zero
    UI changes per the module design goal.

    Clearly NOT for production spend — cards issued here cannot be used
    anywhere. Swap CardConfig.provider_name to a real provider's key before
    launch.
    """
    name = 'mock'

    def _fake_card_number(self):
        last4 = ''.join(random.choices(string.digits, k=4))
        return f"4111 **** **** {last4}"

    def create_card(self, user, funding_amount_usd):
        now = datetime.utcnow()
        expiry = now.replace(year=now.year + 3)
        return {
            'provider_card_id':    'mock_' + ''.join(random.choices(string.ascii_lowercase + string.digits, k=20)),
            'card_number_masked':  self._fake_card_number(),
            'card_brand':          'Visa',
            'expiry_month':        f"{expiry.month:02d}",
            'expiry_year':         str(expiry.year),
            'cardholder_name':     (user.name or 'CARD HOLDER').upper()[:26],
            'balance':             round(funding_amount_usd, 2),
        }

    def fund_card(self, provider_card_id, amount_usd):
        # A real provider call would return the issuer's own new balance;
        # here we simply trust the caller's math since there's no real
        # issuer ledger to reconcile against.
        return amount_usd

    def freeze_card(self, provider_card_id):
        return True

    def unfreeze_card(self, provider_card_id):
        return True

    def terminate_card(self, provider_card_id):
        return 0.0

    def get_balance(self, provider_card_id):
        return None  # no independent issuer ledger to check in mock mode

    def get_transactions(self, provider_card_id):
        return []


# ── registry ─────────────────────────────────────────────────────────────
# Add a real implementation here (e.g. 'sudo': SudoCardProvider) once you
# have provider credentials — see the module docstring above.
PROVIDERS = {
    'mock': MockCardProvider,
}


def get_provider(name):
    cls = PROVIDERS.get(name, MockCardProvider)
    return cls()
