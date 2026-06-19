"""Enterprise layer: audit trail, structured logging, startup diagnostics.

These components attach to the EventBus and observe the pipeline; they never
alter behaviour, so they can be enabled/disabled by policy without risk.
"""
