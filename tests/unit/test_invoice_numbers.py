"""Kevin's invoice number: <MMDDYY><client code>-<consultant code>.

The rules are in docs/engagement-list.md and decision 27. The date is the end
of the billing period, the client code is Kevin's own two letters, and the
consultant is their initials unless two people at that client share them.
"""

from datetime import date

from finance_ops_agent.domain.invoice_numbers import (
    MAX_LENGTH,
    codes_for_client,
    initials,
    invoice_number,
    with_last_name,
)

AUG_END = date(2026, 8, 31)


class TestInitials:
    def test_first_and_last(self) -> None:
        assert initials("Priya Shah") == "PS"

    def test_a_middle_name_is_not_in_them(self) -> None:
        assert initials("Priya Anne Shah") == "PS"

    def test_punctuation_in_a_name_is_ignored(self) -> None:
        assert initials("Siobhan O'Brien") == "SO"
        assert initials("Ana Smith-Jones") == "AS"

    def test_a_name_with_nothing_to_go_on_is_not_guessed_at(self) -> None:
        """The failure path: no initials rather than wrong ones, which becomes
        a LIST_ROW_PROBLEM asking Kevin to fill in the Initials column."""
        assert initials("Prince") is None
        assert initials("") is None

    def test_the_longer_form(self) -> None:
        assert with_last_name("Priya Shah") == "PSHAH"
        assert with_last_name("Paul Singh") == "PSINGH"
        assert with_last_name("Prince") is None


class TestCodesForOneClient:
    def test_initials_when_nobody_clashes(self) -> None:
        codes = codes_for_client({"Priya Shah": "", "Dana Okafor": ""})
        assert codes == {"Priya Shah": "PS", "Dana Okafor": "DO"}

    def test_two_consultants_sharing_initials_both_get_their_last_name(self) -> None:
        """Both change, not just the newcomer: numbers that differ only by who
        Kevin entered first would be worse than either."""
        codes = codes_for_client({"Priya Shah": "", "Paul Singh": ""})
        assert codes == {"Priya Shah": "PSHAH", "Paul Singh": "PSINGH"}

    def test_only_the_consultants_who_clash_change(self) -> None:
        codes = codes_for_client({"Priya Shah": "", "Paul Singh": "", "Dana Okafor": ""})
        assert codes["Dana Okafor"] == "DO"

    def test_kevins_own_initials_win_over_both(self) -> None:
        codes = codes_for_client({"Priya Shah": "PRS", "Paul Singh": ""})
        assert codes == {"Priya Shah": "PRS", "Paul Singh": "PS"}

    def test_a_name_that_gives_nothing_is_left_out(self) -> None:
        assert codes_for_client({"Prince": "", "Priya Shah": ""}) == {"Priya Shah": "PS"}


class TestTheNumber:
    def test_the_worked_example(self) -> None:
        assert invoice_number(AUG_END, "AC", "PS") == "083126AC-PS"

    def test_the_date_is_the_end_of_the_period_whenever_it_is_invoiced(self) -> None:
        """An August timesheet invoiced in September still reads 083126."""
        assert invoice_number(date(2026, 8, 31), "MT", "PS") == "083126MT-PS"
        assert invoice_number(date(2026, 9, 30), "MT", "PS") == "093026MT-PS"

    def test_a_replacement_takes_the_next_one_along(self) -> None:
        assert invoice_number(AUG_END, "AC", "PS", attempt=2) == "083126AC-PS-2"
        assert invoice_number(AUG_END, "AC", "PS", attempt=3) == "083126AC-PS-3"

    def test_a_long_last_name_is_shortened_to_what_quickbooks_allows(self) -> None:
        number = invoice_number(AUG_END, "MT", "PPAPADOPOULOS")
        assert len(number) <= MAX_LENGTH
        assert number.startswith("083126MT-PPAPAD")

    def test_even_a_replacement_of_a_long_one_fits(self) -> None:
        assert len(invoice_number(AUG_END, "MT", "PPAPADOPOULOS", attempt=2)) <= MAX_LENGTH
