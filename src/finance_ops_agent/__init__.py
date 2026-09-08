"""Billing agent for Icon Technologies.

Reads consultant timesheets from email, prepares client invoices, and tells
Kevin what consultants are owed. See docs/how-it-works.md for the process.
"""

from importlib.metadata import version

__version__ = version("finance-ops-agent")
