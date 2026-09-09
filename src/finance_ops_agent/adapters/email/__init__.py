"""The real mailbox: IMAP to read, SMTP to send, at Rackspace Email.

Per docs/integrations/email-imap-smtp.md. `client.py` opens the connections
(TLS verification never disabled), `parse.py` turns a raw message into the
domain's InboundEmail (shared with the fake mailbox), `inbox.py` is the
EmailInbox port, `sender.py` the EmailSender port.
"""
