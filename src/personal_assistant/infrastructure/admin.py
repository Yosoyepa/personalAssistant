"""Read-only local admin dashboard for the in-memory runtime.

Facade module: the implementation was split in phase 14 into focused
``admin_*`` siblings (auth, time, text, redaction, shared, items, trace
categorization/filters, error rows, data fetchers, and HTML rendering) so
each file stays under the mutation-site budget. This module preserves the
public import surface: existing consumers keep importing from
``personal_assistant.infrastructure.admin``.
"""

from __future__ import annotations

from personal_assistant.infrastructure.admin_auth import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    clamp_limit,
    is_local_client,
    local_admin_principal,
)
from personal_assistant.infrastructure.admin_data import AdminDashboard
from personal_assistant.infrastructure.admin_render import render_dashboard_html
from personal_assistant.infrastructure.admin_shared import (
    CONTEXT_UTILIZATION_ATTENTION_THRESHOLD,
    percentile_nearest_rank,
)

__all__ = [
    "CONTEXT_UTILIZATION_ATTENTION_THRESHOLD",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "AdminDashboard",
    "clamp_limit",
    "is_local_client",
    "local_admin_principal",
    "percentile_nearest_rank",
    "render_dashboard_html",
]
