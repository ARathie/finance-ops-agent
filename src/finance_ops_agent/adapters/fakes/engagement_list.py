"""In-memory engagement list for tests."""

from finance_ops_agent.domain.engagements import RawWorkbook


class FakeEngagementList:
    def __init__(self, workbook: RawWorkbook) -> None:
        self.workbook = workbook

    def load(self) -> RawWorkbook:
        return self.workbook
