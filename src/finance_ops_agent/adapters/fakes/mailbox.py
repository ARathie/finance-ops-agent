"""A fake mailbox built from a folder of .eml files, sorted by filename.

The files are parsed exactly as the real adapter parses raw IMAP messages
(adapters/email/parse.py), so the Message-ID is the key here too. The position
is how many messages have been handed over. `move` just records the folder,
which tests can inspect.
"""

from pathlib import Path

from finance_ops_agent.adapters.email.parse import ParsedMessage, parse_message
from finance_ops_agent.domain.messages import InboundEmail


class FakeMailbox:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.folders: dict[str, str] = {}

    def _parsed(self) -> list[ParsedMessage]:
        return [parse_message(path.read_bytes()) for path in sorted(self.directory.glob("*.eml"))]

    def new_messages(self, position: str | None) -> tuple[list[InboundEmail], str]:
        parsed = self._parsed()
        start = int(position) if position else 0
        return [entry.email for entry in parsed[start:]], str(len(parsed))

    def download_attachment(self, message_id: str, attachment_id: str) -> bytes:
        for entry in self._parsed():
            if entry.email.message_id == message_id:
                return entry.attachment_content[attachment_id]
        raise KeyError(attachment_id)

    def move(self, message_id: str, folder: str) -> None:
        self.folders[message_id] = folder
