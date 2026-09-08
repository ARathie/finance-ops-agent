"""The Claude adapter: reading timesheets, classifying emails, reading Kevin's replies.

The technical contract is docs/integrations/claude-extraction.md. The model
reads; it never decides: it never sums hours, never sees a rate, never picks
recipients, and is never given tools. Email content is untrusted input.
"""
