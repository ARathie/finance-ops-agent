"""Reading the agent's mailbox over IMAP (the EmailInbox port).

The position is `(UIDVALIDITY, last UID seen)` for INBOX. UIDs only grow within
one UIDVALIDITY; if the server changes it, the run reads the folder from the
beginning again, which is safe because messages already stored are skipped by
their Message-ID. Messages are fetched with BODY.PEEK so the agent never marks
mail as read by itself.
"""

import json
from collections.abc import Callable
from datetime import date, datetime

from imapclient import IMAPClient

from finance_ops_agent.adapters.email.client import (
    Folders,
    MailAccount,
    MailboxProblem,
    open_imap,
)
from finance_ops_agent.adapters.email.parse import ParsedMessage, parse_message
from finance_ops_agent.domain.messages import InboundEmail

INBOX = "INBOX"


def encode_position(uidvalidity: int, last_uid: int) -> str:
    return json.dumps({"uidvalidity": uidvalidity, "last_uid": last_uid})


def decode_position(position: str | None, uidvalidity: int) -> int:
    """The last UID seen, or 0 when there is no position or the folder was rebuilt."""
    if not position:
        return 0
    try:
        data = json.loads(position)
    except ValueError:
        return 0
    if int(data.get("uidvalidity", -1)) != uidvalidity:
        return 0
    return int(data.get("last_uid", 0))


class ImapInbox:
    def __init__(
        self,
        account: MailAccount,
        agent_address: str,
        start_date: date | None,
        connect: Callable[[MailAccount], IMAPClient] = open_imap,
    ) -> None:
        self._account = account
        self._agent = agent_address.strip().casefold()
        self._start_date = start_date
        self._connect = connect
        self._cache: dict[str, ParsedMessage] = {}

    def new_messages(self, position: str | None) -> tuple[list[InboundEmail], str]:
        client = self._connect(self._account)
        try:
            info = client.select_folder(INBOX, readonly=True)
            uidvalidity = int(info[b"UIDVALIDITY"])
            last = decode_position(position, uidvalidity)
            criteria: list[object] = ["UID", f"{last + 1}:*"]
            if self._start_date is not None:
                criteria += ["SINCE", self._start_date]
            # "n:*" also returns the newest message when n is past the end.
            uids = sorted(int(uid) for uid in client.search(criteria) if int(uid) > last)
            emails: list[InboundEmail] = []
            newest = last
            for uid in uids:
                parsed = self._fetch(client, uid)
                newest = max(newest, uid)
                if parsed.email.from_address.casefold() == self._agent:
                    continue  # the agent's own mail, quoted back by a bounce or auto-reply
                self._cache[parsed.email.message_id] = parsed
                emails.append(parsed.email)
            return emails, encode_position(uidvalidity, newest)
        finally:
            client.logout()

    def _fetch(self, client: IMAPClient, uid: int) -> ParsedMessage:
        data = client.fetch([uid], ["BODY.PEEK[]", "INTERNALDATE"])[uid]
        raw = data[b"BODY[]"]
        internal = data.get(b"INTERNALDATE")
        assert isinstance(raw, bytes)
        return parse_message(raw, internal if isinstance(internal, datetime) else None)

    def _find(self, client: IMAPClient, message_id: str) -> list[int]:
        return [int(uid) for uid in client.search(["HEADER", "Message-ID", message_id])]

    def download_attachment(self, message_id: str, attachment_id: str) -> bytes:
        parsed = self._cache.get(message_id)
        if parsed is None:
            client = self._connect(self._account)
            try:
                client.select_folder(INBOX, readonly=True)
                uids = self._find(client, message_id)
                if not uids:
                    raise MailboxProblem(f"message {message_id} is no longer in the inbox")
                parsed = self._fetch(client, uids[0])
                self._cache[message_id] = parsed
            finally:
                client.logout()
        return parsed.attachment_content[attachment_id]

    def move(self, message_id: str, folder: str) -> None:
        client = self._connect(self._account)
        try:
            client.select_folder(INBOX, readonly=False)
            uids = self._find(client, message_id)
            if not uids:
                return  # already moved by a hand, or never had a real Message-ID
            target = Folders(client, self._account.folder_prefix).agent_folder(folder)
            if client.has_capability("MOVE"):
                client.move(uids, target)
            else:
                client.copy(uids, target)
                client.delete_messages(uids)
                client.expunge()
        finally:
            client.logout()
