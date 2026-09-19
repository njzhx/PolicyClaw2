from bs4 import BeautifulSoup

from crawler_core import extract_content_text


def _node(html):
    return BeautifulSoup(html, "html.parser").div


def test_extract_content_text_preserves_paragraphs():
    node = _node("<div><p>第一段</p><p>第二段</p></div>")
    assert extract_content_text(node) == "第一段\n第二段"


def test_extract_content_text_keeps_inline_spans_together():
    node = _node("<div><p><span>江苏省</span><span>工业和信息化厅</span></p></div>")
    assert extract_content_text(node) == "江苏省工业和信息化厅"


def test_extract_content_text_preserves_br_and_list_items():
    node = _node("<div>第一行<br>第二行<ul><li>事项一</li><li>事项二</li></ul></div>")
    assert extract_content_text(node) == "第一行\n第二行\n事项一\n事项二"


def test_extract_content_text_separates_table_cells():
    node = _node("<div><table><tr><td>名称</td><td>数值</td></tr></table></div>")
    assert extract_content_text(node) == "名称\t数值"


def test_extract_content_text_ignores_script_and_style():
    node = _node("<div><!-- marker --><style>.x{}</style><p>正文</p><script>alert(1)</script></div>")
    assert extract_content_text(node) == "正文"
