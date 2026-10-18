import unittest
from text_import import parse_text,parse_row

class TextImporterTests(unittest.TestCase):
    def test_pipe_report(self):
        text = """## 全台データ一覧・差枚ランキング
順位 | 台番 | 機種 | 差枚/差玉 | G数 | 出率 | BB | RB | ART
1 | 760 | 戦国コレクション6 | +12,139 | 8,114 | 149.9% | - | - | -
2 | 873 | スマスロ マギアレコード 魔法少女まどか☆マギカ外伝 | -7,407 | 5,774 | 57.2% | - | - | -
平均 | +2,366 | - | -
"""
        records,notes = parse_text(text,"tsurugamine","2026-07-26")
        self.assertEqual(len(records),2)
        self.assertEqual(records[0]["diff"],12139)
        self.assertEqual(records[1]["diff"],-7407)
        self.assertEqual(records[1]["games"],5774)
        self.assertEqual(notes,[])

    def test_tab_report(self):
        text = "1\t521\tマイジャグラーV\t+1,900\t5,600\t111.3%\t23\t22\n"
        rows,_ = parse_text(text,"fujisawa","2026-10-18")
        self.assertEqual(rows[0]["bb"],23)
        self.assertEqual(rows[0]["rb"],22)

    def test_single_space_row(self):
        row=parse_row("1  521  マイジャグラーV  +1,900  5,600  111.3%", "fujisawa","2026-10-18")
        self.assertEqual(row["games"],5600)

    def test_fake_plus_one(self):
        lines=["順位 | 台番 | 機種 | 差枚/差玉 | G数 | 出率"]
        lines.extend(f"{i} | {1000+i} | マイジャグラーV | +1 | 5,600 | 100.0%" for i in range(1,12))
        rows,notes=parse_text("\n".join(lines),"fujisawa","2026-08-20")
        self.assertEqual(len(rows),11)
        self.assertTrue(all(r["diff"] is None for r in rows))
        self.assertTrue(notes)

    def test_deduplicates(self):
        text="1 | 760 | 戦国コレクション6 | 1,000 | 8,000 | 104%\n2 | 760 | 戦国コレクション6 | 2,000 | 8,100 | 108%"
        rows,_=parse_text(text,"tsurugamine","2026-07-26")
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["games"],8100)

    def test_empty_invalid(self):
        with self.assertRaises(ValueError):
            parse_text("これはレポートではありません","fujisawa","2026-10-18")

if __name__=="__main__":
    unittest.main()
