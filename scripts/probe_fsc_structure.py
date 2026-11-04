"""Temporary read-only HTML-layout probe; never prints or stores article bodies."""

from html.parser import HTMLParser
import json
from pathlib import Path
import sys
from urllib.request import Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from weekly_trend.article_sources import _ArticleRedirect, article_url
from weekly_trend.collectors import _read_response, TIMEOUT_SECONDS

URL = "https://www.fsc.go.kr/no010101/88054"
SOURCE = {"id": "fsc-press-releases", "type": "html", "adapter": "fsc_html",
          "enabled": True, "allowed_hosts": ["www.fsc.go.kr"]}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
WORDS = ("바이낸스", "콜드월렛", "가상자산사업자", "보전")


class Layout(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.nodes, self.meta = [], [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and attrs.get("property") in {"og:title", "og:url"}:
            self.meta.append({"property": attrs["property"], "content": attrs.get("content", "")[:500]})
        if tag == "link" and attrs.get("rel") == "canonical":
            self.meta.append({"rel": "canonical", "same_identity": article_url(attrs.get("href", ""), SOURCE) == URL})
        if tag in VOID:
            return
        classes = attrs.get("class", "")[:200]
        node = {"tag": tag, "id": attrs.get("id", "")[:120], "classes": classes,
                "text_chars": 0, "keywords": set(), "sample": "",
                "is_title": tag in {"title", "h1", "h2", "h3"} or any(word in classes.split() for word in {"subject", "title", "tit"})}
        node["ancestors"] = [{"tag": p["tag"], "id": p["id"], "classes": p["classes"]} for p in self.stack[-10:]]
        self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                self.nodes.extend(self.stack[index:])
                del self.stack[index:]
                break

    def handle_data(self, data):
        clean = " ".join(data.split())
        for node in self.stack:
            node["text_chars"] += len(clean)
            node["keywords"].update(word for word in WORDS if word in clean)
            if node["is_title"]:
                node["sample"] = (node["sample"] + clean)[:500]


def main():
    expected = article_url(URL, SOURCE, resolve=True)
    opener = build_opener(_ArticleRedirect(expected, SOURCE))
    try:
        with opener.open(Request(expected, headers={"Accept": "text/html", "Accept-Encoding": "identity"}), timeout=TIMEOUT_SECONDS) as response:
            if article_url(response.geturl(), SOURCE) != expected or response.status != 200:
                raise ValueError("unexpected_response")
            data = _read_response(response)
            html = data.decode("utf-8")
        parser = Layout()
        parser.feed(html)
        parser.close()
        parser.nodes.extend(parser.stack)
        rows = [node for node in parser.nodes if node["text_chars"] >= 100 and node["keywords"] and node["tag"] in {"main", "article", "section", "div", "p"}]
        rows.sort(key=lambda node: node["text_chars"])
        public = [{key: (sorted(value) if key == "keywords" else value) for key, value in node.items() if key not in {"sample", "is_title"}} for node in rows[:45]]
        titles = [{key: node[key] for key in ("tag", "id", "classes", "sample")} for node in parser.nodes if node["is_title"] and node["sample"]]
        print(json.dumps({"status": "success", "response_bytes": len(data), "meta": parser.meta, "titles": titles[:25], "content_structure": public}, ensure_ascii=False, indent=2))
    except Exception as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__, "http_code": getattr(error, "code", None)}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
