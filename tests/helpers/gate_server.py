"""Runs the shared server with a stand-in for Windows sign-in, for tests on any OS.

A Negotiate token of b"user:DOMAIN\\name" signs that account in at once. b"two-step" asks for a second
round (as NTLM does), and the second token names the account. Group membership comes from GROUPS.
"""

import sys

import uvicorn

from pi_compression_tuner import serve
from pi_compression_tuner.serve import Step

GROUPS = {"CORP\\PI Engineers": {"CORP\\carol"}}


class FakeSignIn:
    def __init__(self):
        self.round = 0

    def step(self, token: bytes) -> Step:
        self.round += 1
        if token == b"two-step":
            return Step(False, b"challenge")
        if not token.startswith(b"user:"):
            raise PermissionError("logon denied")
        user = token[5:].decode()
        return Step(True, b"mutual", user, lambda name: user in GROUPS.get(name, set()))


if __name__ == "__main__":
    app = serve.build_app(sign_in=True, new_attempt=FakeSignIn)
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
