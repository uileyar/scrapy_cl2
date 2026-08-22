from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rmdown_download import _is_cf_challenge_error, download_from_rmdown_url


CF_ERR = RuntimeError(
    "HTTP 403: Cloudflare 人机验证拦截（Just a moment...），"
    "curl_cffi impersonate=chrome 仍未通过"
)


class TestRmdownCfNoRetry(unittest.TestCase):
    def test_is_cf_challenge_error(self) -> None:
        self.assertTrue(_is_cf_challenge_error(CF_ERR))
        self.assertTrue(
            _is_cf_challenge_error(
                RuntimeError("download.php 返回 Cloudflare 人机验证页，不是种子文件")
            )
        )
        self.assertFalse(_is_cf_challenge_error(RuntimeError("HTTP 502: bad gateway")))

    def test_cf_challenge_does_not_retry(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with mock.patch(
                "rmdown_download._download_once", side_effect=CF_ERR
            ) as once, mock.patch("rmdown_download.time.sleep") as sleep:
                with self.assertRaises(RuntimeError) as ctx:
                    download_from_rmdown_url(
                        "https://www.rmdown.com/link.php?hash=abc",
                        td,
                        retries=3,
                        fallback_direct=False,
                    )
        self.assertEqual(once.call_count, 1)
        sleep.assert_not_called()
        self.assertIn("人机验证", str(ctx.exception))
        self.assertNotIn("已重试", str(ctx.exception))
