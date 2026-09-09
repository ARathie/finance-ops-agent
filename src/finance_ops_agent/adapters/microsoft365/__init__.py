"""The Microsoft 365 mailbox, through Microsoft Graph.

Setup, endpoints, and throttling rules are in
docs/integrations/microsoft-365-email.md. `msal` gets the token; a small
httpx client calls the handful of endpoints the agent needs, so every call
can be recorded and replayed in tests.
"""
