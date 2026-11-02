from __future__ import annotations

import io
import unittest

from xraydo.countries import (
    detect_countries,
    detect_public_location,
    format_key_name,
    format_remark,
    get_country,
    load_countries,
    normalize_country_text,
)


class CountryTests(unittest.TestCase):
    def test_catalog_has_unique_iso_flags_names_and_aliases(self) -> None:
        countries = load_countries()
        codes = [country["iso"] for country in countries]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertGreaterEqual(len(countries), 50)
        for country in countries:
            self.assertRegex(country["iso"], r"^[A-Z]{2}$")
            self.assertEqual(len(country["flag"]), 2)
            self.assertEqual(set(country["names"]), {"en", "ru"})
            self.assertTrue(country["aliases"])

    def test_documented_english_russian_iso_and_hyphen_examples(self) -> None:
        examples = {
            "Germany": "DE",
            "Германия": "DE",
            "DE Frankfurt": "DE",
            "Turkey-01": "TR",
        }
        for value, expected in examples.items():
            with self.subTest(value=value):
                self.assertEqual([item["iso"] for item in detect_countries(value)], [expected])

    def test_search_normalizes_unicode_case_spaces_hyphens_and_underscores(self) -> None:
        self.assertEqual(normalize_country_text("  TÜRKİYE__01  "), "turkiye 01")
        self.assertEqual(detect_countries("TÜRKİYE__01")[0]["iso"], "TR")
        self.assertEqual(detect_countries("Germany__Frankfurt")[0]["iso"], "DE")
        self.assertEqual(detect_countries("НОВАЯ---ЗЕЛАНДИЯ")[0]["iso"], "NZ")

    def test_ambiguous_alias_is_not_silently_selected(self) -> None:
        self.assertEqual([item["iso"] for item in detect_countries("Congo-01")], ["CD", "CG"])
        self.assertEqual(detect_countries("private-edge-01"), [])

    def test_flag_is_added_once_and_wrong_leading_flag_is_replaced(self) -> None:
        germany = get_country("de")
        self.assertIsNotNone(germany)
        assert germany is not None
        self.assertEqual(format_remark("Germany", germany), "🇩🇪 Germany")
        self.assertEqual(format_remark("🇩🇪 Germany", germany), "🇩🇪 Germany")
        self.assertEqual(format_remark("🇫🇷 Germany", germany), "🇩🇪 Germany")

    def test_key_name_contains_localized_country_and_preserves_device(self) -> None:
        germany = get_country("DE")
        poland = get_country("PL")
        assert germany is not None
        assert poland is not None
        self.assertEqual(format_key_name("phone", germany, "en"), "🇩🇪 Germany · phone")
        self.assertEqual(format_key_name("phone", germany, "ru"), "🇩🇪 Германия · phone")
        self.assertEqual(
            format_key_name("🇩🇪 Germany · phone", germany, "ru"),
            "🇩🇪 Германия · phone",
        )
        self.assertEqual(
            format_key_name("🇩🇪 Germany · phone", poland, "ru"),
            "🇵🇱 Польша · phone",
        )

    def test_public_location_uses_validated_ip_and_country(self) -> None:
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        def opener(request, timeout):
            self.assertEqual(timeout, 0.5)
            if request.full_url == "https://geo.example/":
                return Response(b'{"ip":"8.8.8.8","country":"DE"}')
            self.assertEqual(request.full_url, "https://rdap.example/ip/8.8.8.8")
            return Response(b'{"country":"PL"}')

        self.assertEqual(
            detect_public_location(
                url="https://geo.example/",
                rdap_url="https://rdap.example/ip/{ip}",
                timeout=0.5,
                opener=opener,
            ),
            {
                "ip": "8.8.8.8",
                "country": "PL",
                "geoip_country": "DE",
                "source": "https://rdap.example/ip/8.8.8.8",
                "version": "2",
            },
        )

    def test_public_location_falls_back_to_geoip_when_rdap_is_disabled(self) -> None:
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        result = detect_public_location(
            url="https://geo.example/",
            rdap_url="off",
            opener=lambda _request, timeout: Response(
                b'{"ip":"8.8.8.8","country":"DE"}'
            ),
        )
        self.assertEqual(result["country"], "DE")
        self.assertEqual(result["source"], "https://geo.example/")
        self.assertEqual(result["version"], "2")
