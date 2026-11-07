"""Parser strony produktu Calavado (ecommerce_price_comparer/spiders/Calavado.py)."""
import json

from scrapy.http import HtmlResponse

from ecommerce_price_comparer.spiders.Calavado import CalavadoSpider
from ecommerce_price_comparer.utilities.data_utility import DataCleaner

PRODUCT_URL = "https://www.calvado.com/produkt.html"


def product_page(name, sku, body, price="319.00"):
    jsonld = {
        "@context": "https://schema.org",
        "@graph": [{
            "@type": "Product",
            "name": name,
            "sku": sku,
            "category": "Koce",
            "brand": {"@type": "Brand", "name": "Ibena"},
            "offers": {"@type": "Offer", "price": price, "availability": "https://schema.org/InStock"},
            "url": PRODUCT_URL,
        }],
    }
    html = f"""<html><head>
    <script type="application/ld+json">{json.dumps(jsonld)}</script>
    </head><body><div class="product-essential">
      <div class="product-shop">
        <div class="product-name"><h1>{name}</h1></div>
        {body}
      </div>
      <p>Kolor</p><p>Wielobarwna tonacja</p>
    </div></body></html>"""
    return HtmlResponse(url=PRODUCT_URL, body=html.encode("utf-8"), encoding="utf-8")


def parse(response):
    return list(CalavadoSpider().parse_json(response))


class TestSingleSizeProduct:
    """Produkt w jednym rozmiarze nie ma tabeli wariantów i wcześniej przepadał bez ostrzeżenia."""

    def test_regular_price(self):
        response = product_page("Koc Ibena Alexandria Desert 150x200", "IB0474-100", """
            <div class="price-box">
              <span class="regular-price"><span class="price">319,00 zł</span></span>
            </div>""")

        items = parse(response)

        assert len(items) == 1
        item = items[0]
        assert item["name"] == "Koc Ibena Alexandria Desert 150x200"
        assert item["size"] == "150x200"
        assert item["sku"] == "IB0474100-150x200"
        assert item["price_normal"] == "319,00 zł"
        assert item["price_special"] is None
        assert item["manufacturer"] == "Ibena"
        assert item["url"] == PRODUCT_URL
        assert item["store"] == "calavado"

    def test_discounted_price(self):
        response = product_page("Koc Biederlack Aurora Lines 150x200", "832764", """
            <div class="price-box">
              <p class="old-price"><span class="price"> 319,00 zł </span></p>
              <p class="special-price"><span class="price"> 299,50 zł </span></p>
            </div>""")

        item = parse(response)[0]

        assert item["price_normal"].strip() == "319,00 zł"
        assert item["price_special"].strip() == "299,50 zł"

    def test_price_from_jsonld_when_price_box_is_missing(self):
        response = product_page("Koc Ibena Alexandria Desert 150x200", "IB0474-100", "", price="319.00")

        item = parse(response)[0]

        assert item["price_normal"] == "319.00"

    def test_ignores_price_boxes_of_related_products(self):
        response = product_page("Koc Ibena Alexandria Desert 150x200", "IB0474-100", """
            <div class="price-box">
              <span class="regular-price"><span class="price">319,00 zł</span></span>
            </div>""")
        related = b"""<div class="box-related"><div class="price-box">
            <span class="regular-price"><span class="price">9,99 zl</span></span></div></div>"""
        response = response.replace(body=related + response.body)

        item = parse(response)[0]

        assert item["price_normal"] == "319,00 zł"


class TestProductWithSizeTable:

    def test_one_item_per_size_row(self):
        response = product_page("Kołdra FAN Klimakomfort", "F082-grouped", """
            <div class="price-box"><span class="regular-price"><span class="price">od 439,00 zł</span></span></div>
            <table id="super-product-table">
              <tr><th>Produkt</th></tr>
              <tr><td><a href="https://www.calvado.com/koldra-135.html">Kołdra FAN Klimakomfort 135x200</a></td>
                  <td><span class="regular-price"><span class="price">439,00 zł</span></span></td></tr>
              <tr><td><a href="https://www.calvado.com/koldra-155.html">Kołdra FAN Klimakomfort 155x200</a></td>
                  <td><p class="old-price"><span class="price">499,00 zł</span></p>
                      <p class="special-price"><span class="price">464,07 zł</span></p></td></tr>
            </table>""")

        items = parse(response)

        assert [(i["size"], i["sku"], i["price_normal"], i["price_special"]) for i in items] == [
            ("135x200", "F082-135x200", "439,00 zł", None),
            ("155x200", "F082-155x200", "499,00 zł", "464,07 zł"),
        ]
        assert items[0]["url"] == "https://www.calvado.com/koldra-135.html"

    def test_fitted_sheet_keeps_size_range(self):
        response = product_page("Prześcieradło Bella Donna Jersey", "BD-grouped", """
            <table id="super-product-table">
              <tr><td><a href="https://www.calvado.com/bd-90.html">Prześcieradło Bella Donna Jersey marine 90/100x200/220</a></td>
                  <td><span class="regular-price"><span class="price">295,00 zł</span></span></td></tr>
              <tr><td><a href="https://www.calvado.com/bd-140.html">Prześcieradło Bella Donna Jersey marine 140-160 x 200-220</a></td>
                  <td><span class="regular-price"><span class="price">449,00 zł</span></span></td></tr>
            </table>""")

        sizes = [item["size"] for item in parse(response)]

        assert sizes == ["90/100x200/220", "140-160 x 200-220"]
        assert [DataCleaner.standardize_size(size) for size in sizes] == ["90100x200220", "140160x200220"]

    def test_page_without_name_and_price_gives_no_item(self):
        response = product_page("", "X1", "", price=None)

        assert parse(response) == []
