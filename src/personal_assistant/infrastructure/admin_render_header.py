"""Header block renderer (topbar, nav, token field) for the admin dashboard."""

from __future__ import annotations

from html import escape
from typing import Any


def _render_header(meta: dict[str, Any]) -> list[str]:
    return [
        '<header class="topbar">',
        '<div class="container">',
        "<h1>Personal Assistant Admin</h1>",
        f'<p class="muted">Tenant {escape(meta["tenant_id"])} | Principal {escape(meta["principal_id"])} | {escape(meta["generated_at"])}</p>',
        '<nav aria-label="Dashboard sections">',
        '<a href="#health">Health</a>',
        '<a href="#agenda">Agenda</a>',
        '<a href="#reminders">Reminders</a>',
        '<a href="#errors">Errors</a>',
        '<a href="#approvals">Approvals</a>',
        '<a href="#traces">Traces</a>',
        '<a href="#context">Context</a>',
        '<a href="#outbox">Outbox</a>',
        '<a href="#scheduler">Scheduler</a>',
        '<a href="#events">Events</a>',
        '<a href="#states">States</a>',
        '<a href="#memory">Memory</a>',
        "</nav>",
        '<div class="token-bar">',
        '<label for="admin-token">Bearer token</label>',
        '<input id="admin-token" type="password" autocomplete="off" '
        'placeholder="ADMIN_TOKEN" data-admin-token>',
        "</div>",
        "</div>",
        "</header>",
    ]
