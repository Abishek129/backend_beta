"""Screen registry — the single place a ``ws/mfm/<id>/<screen>/`` route is declared.

``routing.py`` builds one URL per entry, so a screen exists only once it is
registered here. Usage (a plain call, NOT a decorator — a screen names one class
per equipment type it serves; unserved types are left out and get a 4404):

    from panels.registry import screen
    from panels.consumers import lt_panel as lt, transformer as tx

    screen('overview', transformer=tx.TransformerOverview, lt_panel=lt.LtPanelOverview)
    screen('real-time-monitoring', distribution_panel=RealTimeMonitoringConsumer)

PIN 2026-09-09: ZERO screens registered — consumers are pending discussion, so
routing yields only the invalid-id catch-all.
"""

# {screen name: {type name: consumer class}} in registration order.
SCREENS: dict[str, dict[str, type]] = {}


def screen(name, *, distribution_panel=None, transformer=None, lt_panel=None):
    """Register the consumer classes serving ``name``. Re-registering replaces."""
    types = {'distribution_panel': distribution_panel,
             'transformer': transformer,
             'lt_panel': lt_panel}
    SCREENS[name] = {k: v for k, v in types.items() if v is not None}
    return SCREENS[name]
