import unittest

from core.pipelines.allocation_report_src import inquiry_to_str


class InquiryToStr(unittest.TestCase):
    def test_string_unchanged(self):
        s = "29-Oct-2026,CM,06436,06436,,K1,C,0,,,,,,,D,01050102"
        self.assertEqual(inquiry_to_str(s), s)

    def test_list_of_objects_becomes_csv(self):
        v = [{"curDate": "29-Oct-2026", "segment": "CO", "cmCode": "12798", "tmCode": "12798",
              "cpCode": "", "cliCode": "PIIO7301", "accType": "C", "amt": 0,
              "filler1": "", "filler2": "", "filler3": "", "filler4": "", "filler5": "",
              "filler6": "", "action": "U", "errCd": "01050100"},
             {"curDate": "29-Oct-2026", "segment": "CM", "cmCode": "12798", "tmCode": "12798",
              "cpCode": "", "cliCode": "J1", "accType": "C", "amt": 1.62,
              "filler1": "", "filler2": "", "filler3": "", "filler4": "", "filler5": "",
              "filler6": "", "action": "D", "errCd": "01050100"}]
        out = inquiry_to_str(v)
        recs = out.split("^")
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0], "29-Oct-2026,CO,12798,12798,,PIIO7301,C,0,,,,,,,U,01050100")
        self.assertEqual(recs[1].split(",")[7], "1.62")
        self.assertEqual(len(recs[1].split(",")), 16)

    def test_empty_and_odd(self):
        self.assertEqual(inquiry_to_str(None), "")
        self.assertEqual(inquiry_to_str([]), "")
        self.assertEqual(inquiry_to_str(5), "")


if __name__ == "__main__":
    unittest.main()
