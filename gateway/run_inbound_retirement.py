"""Messaging admission participates in the native process retirement fence."""
from functools import wraps


def retirement_admission(handler):
    @wraps(handler)
    async def admitted(self, event):
        from hermes_cli.backend_retirement import retirement
        from agent.i18n import t
        with retirement.work() as accepted:
            if not accepted:
                return t("gateway.busy.draining_maintenance")
            return await handler(self, event)
    return admitted
