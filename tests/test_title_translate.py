from __future__ import annotations

import unittest
from unittest import mock

import title_translate
from title_translate import translate_code_title, translate_title_to_zh


GOOGLE_500 = (
    "Error 500 (Server Error)!!1500.That’s an error."
    "There was an error. Please try again later.That’s all we know."
)


class TestTitleTranslate(unittest.TestCase):
    def setUp(self) -> None:
        title_translate._title_zh_ok.clear()

    def test_google_500_page_falls_back_to_original(self) -> None:
        original = "隣の人妻は僕の元カノ"
        fake = mock.Mock()
        fake.translate.return_value = GOOGLE_500
        with mock.patch("title_translate._google_tl", return_value=fake), mock.patch(
            "title_translate.time.sleep"
        ):
            self.assertEqual(translate_code_title(original), original)
        self.assertNotIn(original, title_translate._title_zh_ok)

    def test_google_500_is_not_cached_as_translation(self) -> None:
        original = "隣の人妻は僕の元カノ"
        fake = mock.Mock()
        fake.translate.return_value = GOOGLE_500
        with mock.patch("title_translate._google_tl", return_value=fake), mock.patch(
            "title_translate.time.sleep"
        ):
            self.assertEqual(translate_title_to_zh(original), "")
        self.assertNotIn(original, title_translate._title_zh_ok)

    def test_real_translation_still_used(self) -> None:
        original = "隣の人妻は僕の元カノ"
        fake = mock.Mock()
        fake.translate.return_value = "隔壁人妻是我前女友"
        with mock.patch("title_translate._google_tl", return_value=fake), mock.patch(
            "title_translate.time.sleep"
        ):
            self.assertEqual(translate_code_title(original), "隔壁人妻是我前女友")
