"""TCBSBroker STUB. Makes no network calls, reads no env/secrets; every method raises.

Planned auth flow (from the T56 design, NOT yet verified against TCBS docs):
  1. API key (from a secret store, never the repo) + a human-entered OTP.
  2. POST /gaia/v1/oauth2/openapi/token -> JWT with an expiry.
  3. The token endpoint allows only ~10 calls/day, so cache the JWT and its `exp` in a
     0600 file outside the repo; reuse until exp - 5 min; count token calls per ICT day and
     refuse from the 8th. On 401/429 do not loop: stop and report AUTH_REQUIRED.
  4. All requests share a token bucket of <= 8 req/s (limit is ~10/s).
  5. Never log the key, JWT or OTP.
"""

from broker.base import Broker


class TCBSBroker(Broker):
    def __init__(self, *args, **kwargs):
        raise NotImplementedError("TCBSBroker is a stub: not connected")

    def authenticate(self, otp):
        raise NotImplementedError("TCBSBroker is a stub: not connected")

    def get_cash(self):
        raise NotImplementedError("TCBSBroker is a stub: not connected")

    def get_positions(self, today=None):
        raise NotImplementedError("TCBSBroker is a stub: not connected")

    def place_order(self, order):
        raise NotImplementedError("TCBSBroker is a stub: not connected")

    def cancel(self, client_id):
        raise NotImplementedError("TCBSBroker is a stub: not connected")
