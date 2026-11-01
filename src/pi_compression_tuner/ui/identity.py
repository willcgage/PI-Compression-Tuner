"""Who is using this page.

On a shared server (``pict serve``) the sign-in gate tells the page who is signed in and whether they are an
admin. Run on one's own machine (``pict ui``) there is no sign-in: the person at the keyboard has every right,
as before.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import streamlit as st

from pi_compression_tuner.access import ADMIN
from pi_compression_tuner.serve import SIGNIN_ENV, grant_from_headers


@dataclass(frozen=True)
class Viewer:
    user: str          # DOMAIN\name; empty without sign-in
    is_admin: bool
    via: str = ""      # the admins or users entry that let them in
    shared: bool = False


def headers():
    try:
        return st.context.headers
    except Exception:
        return {}


def viewer() -> Viewer | None:
    """The person using this page; None on a shared server when the gate did not identify them."""
    if not os.environ.get(SIGNIN_ENV):
        return Viewer("", True)
    grant = grant_from_headers(headers())
    if grant is None:
        return None
    return Viewer(grant.user, grant.role == ADMIN, grant.via, True)
