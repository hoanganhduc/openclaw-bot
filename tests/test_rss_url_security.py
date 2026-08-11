#!/usr/bin/env python3
import importlib.util
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "workspace/skills/rss-news-digest/rss_news_digest.py"
SPEC = importlib.util.spec_from_file_location("openclaw_rss_security_test", RUNTIME)
assert SPEC is not None and SPEC.loader is not None
RSS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RSS
SPEC.loader.exec_module(RSS)


def answer(address, *, family=socket.AF_INET):
    if family == socket.AF_INET6:
        sockaddr = (address, 443, 0, 0)
    else:
        sockaddr = (address, 443)
    return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)]


class RssUrlSecurityTests(unittest.TestCase):
    def assert_blocked(self, url, addresses=None):
        resolver = None
        if addresses is not None:
            resolver = lambda *_args: addresses
        with self.assertRaises(RSS.FeedFetchError):
            RSS.resolve_feed_target(url, resolver=resolver)

    def test_non_http_credentials_and_control_characters_are_blocked(self):
        self.assert_blocked("file:///etc/passwd")
        self.assert_blocked("gopher://example.com/feed")
        self.assert_blocked("https://user:pass@example.com/feed")
        self.assert_blocked("https://example.com/feed\nHost:internal")

    def test_loopback_private_link_local_metadata_and_ipv6_are_blocked(self):
        for url in (
            "http://127.0.0.1/feed",
            "http://10.0.0.1/feed",
            "http://172.16.0.1/feed",
            "http://192.168.0.1/feed",
            "http://169.254.169.254/latest/meta-data",
            "http://[::1]/feed",
            "http://[fe80::1]/feed",
            "http://[::ffff:169.254.169.254]/feed",
        ):
            with self.subTest(url=url):
                self.assert_blocked(url)

    def test_dns_answers_fail_closed_if_any_address_is_non_public(self):
        mixed = answer("93.184.216.34") + answer("127.0.0.1")
        self.assert_blocked("https://feeds.example/feed", mixed)

    def test_request_receives_only_the_pinned_public_address(self):
        calls = []

        def resolver(*_args):
            calls.append("resolved")
            return answer("93.184.216.34")

        observed = []

        def requester(target):
            observed.append(target)
            return RSS.FeedResponse(200, {"content-type": "application/rss+xml"}, b"<rss/>")

        fetched = RSS.fetch_feed_document(
            "https://feeds.example/feed",
            resolver=resolver,
            requester=requester,
        )
        self.assertEqual(calls, ["resolved"])
        self.assertEqual(observed[0].addresses[0].sockaddr[0], "93.184.216.34")
        self.assertEqual(fetched.body, b"<rss/>")
        self.assertEqual(fetched.headers["content-location"], "https://feeds.example/feed")

    def test_redirect_is_revalidated_and_private_destination_is_blocked(self):
        requests = []

        def resolver(host, *_args):
            if host == "feeds.example":
                return answer("93.184.216.34")
            return answer("127.0.0.1")

        def requester(target):
            requests.append(target.url)
            return RSS.FeedResponse(302, {"location": "http://internal.example/admin"}, b"")

        with self.assertRaises(RSS.FeedFetchError):
            RSS.fetch_feed_document(
                "https://feeds.example/feed",
                resolver=resolver,
                requester=requester,
            )
        self.assertEqual(requests, ["https://feeds.example/feed"])

    def test_public_redirect_is_resolved_and_pinned_again(self):
        resolved = []
        requested = []

        def resolver(host, *_args):
            resolved.append(host)
            address = "93.184.216.34" if host == "one.example" else "93.184.216.35"
            return answer(address)

        def requester(target):
            requested.append((target.url, target.addresses[0].sockaddr[0]))
            if target.host == "one.example":
                return RSS.FeedResponse(301, {"location": "https://two.example/rss"}, b"")
            return RSS.FeedResponse(200, {}, b"<rss/>")

        RSS.fetch_feed_document(
            "https://one.example/start",
            resolver=resolver,
            requester=requester,
        )
        self.assertEqual(resolved, ["one.example", "two.example"])
        self.assertEqual(
            requested,
            [
                ("https://one.example/start", "93.184.216.34"),
                ("https://two.example/rss", "93.184.216.35"),
            ],
        )

    def test_rebinding_after_resolution_cannot_change_the_socket_target(self):
        answers = iter([answer("93.184.216.34"), answer("127.0.0.1")])
        resolver_calls = []

        def resolver(*_args):
            resolver_calls.append(1)
            return next(answers)

        def requester(target):
            self.assertEqual(target.addresses[0].sockaddr[0], "93.184.216.34")
            return RSS.FeedResponse(200, {}, b"<rss/>")

        RSS.fetch_feed_document(
            "https://rebind.example/rss",
            resolver=resolver,
            requester=requester,
        )
        self.assertEqual(len(resolver_calls), 1)

    def test_feed_file_rejects_symlinks_and_oversized_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target.tsv"
            target.write_text("enabled\ttag\tpriority\tkind\turl\tnotes\n", encoding="utf-8")
            linked = root / "feeds.tsv"
            linked.symlink_to(target)
            with self.assertRaises(RSS.FeedDataError):
                RSS.load_feeds(linked)

            oversized = root / "oversized.tsv"
            oversized.write_bytes(b"x" * (RSS.MAX_FEEDS_FILE_BYTES + 1))
            with self.assertRaises(RSS.FeedDataError):
                RSS.load_feeds(oversized)

    def test_feed_file_caps_rows_and_url_length(self):
        with tempfile.TemporaryDirectory() as temporary:
            feeds = Path(temporary) / "feeds.tsv"
            header = "enabled\ttag\tpriority\tkind\turl\tnotes\n"
            rows = [
                f"1\tresearch\t5\tblog\thttps://feed{index}.example/rss\t\n"
                for index in range(RSS.MAX_FEED_ROWS + 1)
            ]
            feeds.write_text(header + "".join(rows), encoding="utf-8")
            with self.assertRaises(RSS.FeedDataError):
                RSS.load_feeds(feeds)

            feeds.write_text(
                header + "1\tresearch\t5\tblog\thttps://example.com/"
                + ("a" * RSS.MAX_FEED_URL_LENGTH)
                + "\t\n",
                encoding="utf-8",
            )
            with self.assertRaises(RSS.FeedDataError):
                RSS.load_feeds(feeds)

    def test_service_has_a_finite_start_deadline(self):
        unit = (ROOT / "systemd/user/rss_news_digest_bot.service").read_text(
            encoding="utf-8"
        )
        self.assertIn("TimeoutStartSec=300", unit)
        self.assertNotIn("InaccessiblePaths=/run/user/%U", unit)

    def test_scheduled_runner_never_migrates_a_missing_feed_file(self):
        runner = (ROOT / "workspace/skills/rss-news-digest/run_and_summarize.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("--require-existing-feeds", runner)
        self.assertIn("--no-write-digest-stubs", runner)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            secret = root / "secret"
            secret.write_text("credential-canary", encoding="utf-8")
            legacy = root / "feeds.txt"
            legacy.symlink_to(secret)
            missing = root / "feeds.tsv"
            with self.assertRaises(RSS.FeedDataError):
                RSS.load_feeds(missing)
            self.assertFalse(missing.exists())
            self.assertEqual(secret.read_text(encoding="utf-8"), "credential-canary")

    def test_manual_legacy_migration_rejects_a_symlinked_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            secret = root / "secret"
            secret.write_text("credential-canary", encoding="utf-8")
            legacy = root / "feeds.txt"
            legacy.symlink_to(secret)
            destination = root / "feeds.tsv"
            with self.assertRaises(RSS.FeedDataError):
                RSS.migrate_legacy_feeds(legacy, destination)
            self.assertFalse(destination.exists())

    def test_bootstrap_never_follows_destination_symlink_or_race(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            secret = root / "secret"
            secret.write_text("credential-canary", encoding="utf-8")
            destination = root / "feeds.tsv"
            destination.symlink_to(secret)
            RSS.ensure_bootstrap_file(destination, "replacement")
            self.assertEqual(secret.read_text(encoding="utf-8"), "credential-canary")

            destination.unlink()
            real_open = RSS.os.open
            raced = False

            def racing_open(path, flags, mode=0o777):
                nonlocal raced
                if not raced and flags & RSS.os.O_CREAT:
                    raced = True
                    Path(path).symlink_to(secret)
                return real_open(path, flags, mode)

            with patch.object(RSS.os, "open", side_effect=racing_open):
                RSS.ensure_bootstrap_file(destination, "replacement")
            self.assertTrue(destination.is_symlink())
            self.assertEqual(secret.read_text(encoding="utf-8"), "credential-canary")


if __name__ == "__main__":
    unittest.main()
