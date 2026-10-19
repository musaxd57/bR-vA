"""Shared login budgets across real SQLite connections and spawned processes.

Unique evidence directories remain intact; no rows, databases or files are
deleted. These tests do not read .env, access the real DB or use a network.
"""
from concurrent.futures import ThreadPoolExecutor
import multiprocessing
from pathlib import Path
import sys
import threading
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flask import Flask
from sqlalchemy import inspect, select
from extensions import db
from models import User
from briva_auth_models import AuthLoginBucket
from briva_totp import AuthError, SharedLoginThrottle, create_auth_schema, init_auth


def make_app(path, create=False):
    app = Flask("login-budget-" + uuid.uuid4().hex)
    app.config.update(SECRET_KEY="synthetic-shared-login-secret-for-process-tests-2026",
        SQLALCHEMY_DATABASE_URI="sqlite:///" + Path(path).as_posix(),
        SQLALCHEMY_ENGINE_OPTIONS={"connect_args": {"timeout": 2}},
        SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    init_auth(app, db, User)
    if create:
        with app.app_context():
            User.__table__.create(bind=db.engine, checkfirst=True)
            create_auth_schema()
    return app


def process_attempts(path, queue, count, attempts, barrier=None):
    """Top-level Windows spawn target; no live app/session crosses processes."""
    try:
        app = make_app(path)
        with app.app_context():
            throttle = SharedLoginThrottle(attempts=attempts, ip_attempts=100,
                                           clock=lambda: 1700000000)
            if barrier is not None:
                barrier.wait(timeout=20)
            outcomes = [throttle.consume("198.51.100.77", "Synthetic-Process-User") for _ in range(count)]
            queue.put({"outcomes": outcomes})
            db.session.remove()
            db.engine.dispose()
    except Exception as error:
        queue.put({"error_class": type(error).__name__})


class SharedLoginLimitTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT / ".verification-data" / ("auth-login-limit-" + uuid.uuid4().hex)
        directory.mkdir(parents=True, exist_ok=False)
        self.path = directory / "synthetic.db"
        self.app = make_app(self.path, create=True)
        self.now = 1700000000
        self.throttle = SharedLoginThrottle(attempts=3, ip_attempts=5, clock=lambda: self.now)

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()

    def consume(self, peer="192.0.2.20", username="Synthetic-User"):
        with self.app.app_context():
            return self.throttle.consume(peer, username)

    def rows(self):
        with self.app.app_context(), db.engine.connect() as connection:
            return [dict(row) for row in connection.execute(select(AuthLoginBucket.__table__)
                .order_by(AuthLoginBucket.id)).mappings()]

    def test_pair_budget_normalizes_username_and_fixed_window_expires_without_row_deletion(self):
        for _ in range(3):
            self.assertEqual(self.consume(), (True, 0))
        self.assertEqual(self.consume(username="  SYNTHETIC-USER "), (False, 300))
        slots = [row["id"] for row in self.rows()]
        self.now += 299
        self.assertEqual(self.consume(), (False, 1))
        self.now += 1
        self.assertEqual(self.consume(), (True, 0))
        self.assertEqual([row["id"] for row in self.rows()], slots)

    def test_ip_general_budget_survives_username_rotation_and_successful_pair_reset(self):
        self.assertTrue(self.consume(username="One")[0])
        self.assertTrue(self.consume(username="One")[0])
        with self.app.app_context():
            self.throttle.successful_login("192.0.2.20", "One")
        self.assertTrue(self.consume(username="One")[0])
        self.assertTrue(self.consume(username="Two")[0])
        self.assertTrue(self.consume(username="Three")[0])
        self.assertEqual(self.consume(username="Four"), (False, 300))
        with self.app.app_context():
            ip_key = self.throttle._keys("192.0.2.20", "One")[0][0]
        self.assertEqual(next(row for row in self.rows() if row["key_hash"] == ip_key)["attempt_count"], 5)

    def test_raw_ip_and_username_never_enter_rows_and_digests_are_keyed(self):
        peer, username = "198.51.100.234", "PRIVATE_PERSON_NAME_MARKER"
        self.consume(peer, username)
        serialized = repr(self.rows())
        self.assertNotIn(peer, serialized)
        self.assertNotIn(username, serialized)
        with self.app.app_context():
            before = self.throttle._keys(peer, username)
            self.app.secret_key = "different-synthetic-secret-for-hmac"
            self.assertNotEqual(before, self.throttle._keys(peer, username))
        self.assertTrue(all(len(row["key_hash"]) == 64 for row in self.rows()))

    def test_capacity_fails_closed_and_expired_aggregate_slots_are_reused_by_update(self):
        self.throttle.max_keys = 2
        self.assertTrue(self.consume()[0])
        original = self.rows()
        self.assertEqual(len(original), 3)  # two aggregate slots and mutex
        self.assertEqual(self.consume("203.0.113.19", "Other"), (False, 300))
        self.assertEqual(self.rows(), original)
        self.now += 301
        self.assertTrue(self.consume("203.0.113.19", "Other")[0])
        rotated = self.rows()
        self.assertEqual([row["id"] for row in rotated], [row["id"] for row in original])
        self.assertNotEqual([row["key_hash"] for row in rotated], [row["key_hash"] for row in original])

    def test_missing_explicit_auth_upgrade_is_503_and_never_performs_ddl(self):
        missing_path = self.path.with_name("missing-schema.db")
        app = make_app(missing_path)
        with app.app_context():
            with self.assertRaises(AuthError) as caught:
                self.throttle.consume("192.0.2.20", "Synthetic-User")
            self.assertEqual(caught.exception.status, 503)
            self.assertEqual(caught.exception.code, "auth_schema_required")
            self.assertEqual(inspect(db.engine).get_table_names(), [])
            db.engine.dispose()

    def test_independent_store_transaction_never_flushes_or_commits_dirty_user(self):
        with self.app.app_context():
            user = User(username="OriginalSynthetic", company="A", role="cashier", password_hash="synthetic-not-a-login")
            db.session.add(user)
            db.session.commit()
            identity = user.id
            user.username = "UncommittedSensitiveChange"
            self.assertIn(user, db.session.dirty)
            self.assertTrue(self.throttle.consume("192.0.2.20", "Synthetic-User")[0])
            self.assertIn(user, db.session.dirty)
            with db.engine.connect() as connection:
                stored = connection.execute(select(User.username).where(User.id == identity)).scalar_one()
            self.assertEqual(stored, "OriginalSynthetic")
            db.session.rollback()
            self.assertEqual(db.session.get(User, identity).username, "OriginalSynthetic")

    def test_busy_store_is_bounded_503_and_does_not_rollback_request_user_transaction(self):
        with self.app.app_context():
            user = User(username="UncommittedSynthetic", company="A", role="cashier", password_hash="synthetic-not-a-login")
            db.session.add(user)
            db.session.flush()  # Keep a real write lock in the request ORM session.
            identity = user.id
            start = time.monotonic()
            with self.assertRaises(AuthError) as caught:
                self.throttle.consume("192.0.2.20", "Synthetic-User")
            self.assertLess(time.monotonic() - start, 2)
            self.assertEqual((caught.exception.status, caught.exception.retry_after), (503, 1))
            self.assertTrue(db.session().in_transaction())
            self.assertIs(db.session.get(User, identity), user)
            db.session.rollback()
            with db.engine.connect() as connection:
                self.assertIsNone(connection.execute(select(User.id).where(User.id == identity)).scalar_one_or_none())

    def test_single_connection_memory_fixture_never_rolls_back_a_pending_user_write(self):
        app = Flask("memory-login-boundary-" + uuid.uuid4().hex)
        app.config.update(SECRET_KEY="synthetic-memory-boundary-secret", SQLALCHEMY_DATABASE_URI="sqlite:///:memory:")
        db.init_app(app)
        init_auth(app, db, User)
        with app.app_context():
            User.__table__.create(bind=db.engine, checkfirst=True)
            create_auth_schema()
            user = User(username="KeepPendingSynthetic", company="A", role="cashier", password_hash="synthetic")
            db.session.add(user)
            db.session.flush()
            identity = user.id
            with self.assertRaises(AuthError) as caught:
                self.throttle.consume("192.0.2.20", "Synthetic-User")
            self.assertEqual(caught.exception.status, 503)
            self.assertTrue(db.session().in_transaction())
            self.assertIs(db.session.get(User, identity), user)
            self.assertEqual(db.session.execute(select(User.id).where(User.id == identity)).scalar_one(), identity)
            db.session.rollback()
            db.engine.dispose()

    def test_two_connections_share_the_same_last_attempt_budget(self):
        self.throttle.attempts = 1
        barrier = threading.Barrier(2)
        def attempt():
            with self.app.app_context():
                barrier.wait(timeout=10)
                return self.throttle.consume("192.0.2.20", "Synthetic-User")[0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _index: attempt(), (0, 1)))
        self.assertEqual(sorted(results), [False, True])
        pairs = [row for row in self.rows() if row["kind"] == "pair"]
        self.assertEqual(pairs[0]["attempt_count"], 1)

    def run_process(self, count, attempts, barrier=None):
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        process = context.Process(target=process_attempts,
            args=(str(self.path), queue, count, attempts, barrier))
        process.start()
        return process, queue

    def receive_process(self, process, queue):
        result = queue.get(timeout=30)
        process.join(timeout=30)
        self.assertFalse(process.is_alive())
        self.assertEqual(process.exitcode, 0)
        self.assertNotIn("error_class", result)
        queue.close()
        return result["outcomes"]

    def test_a_new_process_restart_cannot_reset_an_existing_password_budget(self):
        first = self.receive_process(*self.run_process(1, 1))
        second = self.receive_process(*self.run_process(1, 1))
        self.assertEqual(first, [(True, 0)])
        self.assertEqual(second, [(False, 300)])

    def test_two_spawned_processes_atomically_share_pair_and_ip_buckets(self):
        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(2)
        first = self.run_process(2, 3, barrier)
        second = self.run_process(2, 3, barrier)
        results = self.receive_process(*first) + self.receive_process(*second)
        self.assertEqual(sum(allowed for allowed, _retry in results), 3, results)
        self.assertEqual(sum(not allowed for allowed, _retry in results), 1, results)
        rows = self.rows()
        self.assertEqual(next(row for row in rows if row["kind"] == "pair")["attempt_count"], 3)
        self.assertEqual(next(row for row in rows if row["kind"] == "ip")["attempt_count"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
