import unittest
import urllib.robotparser
from collector import Reader, CollectionError, parse_page, linked_pages, url_allowed, parse_int
from bs4 import BeautifulSoup

class CollectorSafetyTests(unittest.TestCase):
    def test_url_scope(self):
        self.assertTrue(url_allowed("https://daidata.goraggio.com/100812/unit_list?model=X","100812"))
        self.assertFalse(url_allowed("http://daidata.goraggio.com/100812","100812"))
        self.assertFalse(url_allowed("https://evil.example/100812","100812"))
        self.assertFalse(url_allowed("https://daidata.goraggio.com/1008120","100812"))
        self.assertFalse(url_allowed("https://daidata.goraggio.com/100943","100812"))
        self.assertFalse(url_allowed("https://daidata.goraggio.com:8443/100812","100812"))
    def test_parse_tables(self):
        html="""<h2>マイジャグラーV</h2><table><tr><th>台番号</th><th>ゲーム数</th><th>BB</th><th>RB</th><th>差枚数</th></tr>
<tr><td>123</td><td>4,000</td><td>20</td><td>12</td><td>+1,300</td></tr></table>"""
        rows=parse_page(html,"fujisawa","", "2026-10-18")
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["games"],4000)
        self.assertEqual(rows[0]["diff"],1300)
        self.assertEqual(rows[0]["machine"],"マイジャグラーV")
    def test_no_arbitrary_numbers(self):
        html="<table><tr><th>台番</th><th>差枚数</th></tr><tr><td>1</td><td>500</td></tr></table>"
        self.assertEqual(parse_page(html,"fujisawa","機種A","2026-10-18"),[])
    def test_only_same_store_links(self):
        soup=BeautifulSoup('<a href="/100812/unit_list?model=A">A</a><a href="/100943/unit_list">B</a><a href="https://evil.example/100812/unit_list">C</a>',"html.parser")
        out=linked_pages(soup,"100812")
        self.assertEqual(len(out),1)
    def test_robots_disallow(self):
        p=urllib.robotparser.RobotFileParser()
        p.parse(["User-agent: *","Disallow: /"])
        r=Reader(p,"100812")
        with self.assertRaises(CollectionError):
            r.get("https://daidata.goraggio.com/100812")

if __name__=="__main__":
    unittest.main()
