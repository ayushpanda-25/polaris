"""
The /learn route — the Polaris Academy.

The Academy now lives on the Astraios Nexus site (full curriculum in the
site's design system, alongside the portal / quizzes / tests). Every
in-terminal "Academy" link points at /learn, so this route 302-redirects
to the Nexus page — bookmarks and old links keep working.

The previous self-contained LEARN_HTML page lives in git history
(pre-2026-07-20) if a local fallback is ever needed again.
"""
from __future__ import annotations


ACADEMY_URL = "https://astraiosalgo.com/academy/polaris.html"


def register_learn_route(server) -> None:
    """
    Mount the /learn route on a Flask server (the underlying app.server of
    either the local Dash app or the Vercel serverless entry).
    """
    @server.route("/learn")
    def _learn_view():
        from flask import redirect
        return redirect(ACADEMY_URL, code=302)
