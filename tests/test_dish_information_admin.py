"""Exercise the actual review routes without importing app startup or live services."""
import ast
from functools import wraps
from pathlib import Path
import secrets
import unittest
from unittest.mock import Mock
from urllib.parse import urlsplit

from flask import Flask, abort, flash, jsonify, redirect, request, session, url_for

from dish_info_common import safe_url


ROOT = Path(__file__).resolve().parents[1]
RECORD_ID = "b0d04b57-0725-41d5-b9f3-241ba4806bca"
ADMIN_ID = "cf00483f-c96b-412b-b2d2-4a80be25ed80"
REVIEW_URL = "/admin/dish-information"
APPROVE_URL = f"{REVIEW_URL}/{RECORD_ID}/approve"


class FakeDishStoreError(RuntimeError):
    pass


class DishInformationAdminTests(unittest.TestCase):
    def setUp(self):
        self.user = {"id": ADMIN_ID, "is_admin": True}
        self.store = Mock(name="dish_information_store")
        self.store.list.return_value = [{
            "id": RECORD_ID, "verification_status": "pending",
            "historical_background": "Draft for administrator review.",
            "sources": [{"title": "Evidence", "url": "https://example.org/evidence"}],
        }]
        self.get_store = Mock(return_value=self.store)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY="offline-review-test-session-key")
        self.app.add_url_rule("/admin/login", "admin_login", lambda: "Admin sign in")
        self.app.add_url_rule("/admin", "admin_page", lambda: "Admin dashboard")

        def fake_render(template_name, **context):
            # Return only the route's supplied context, as the real renderer would receive it.
            return jsonify(template=template_name, **context)

        namespace = {
            "app": self.app, "wraps": wraps, "secrets": secrets,
            "abort": abort, "flash": flash, "redirect": redirect,
            "request": request, "session": session, "url_for": url_for,
            "render_template": fake_render, "current_user": lambda: self.user,
            "get_dish_information_store": self.get_store,
            "DishStoreError": FakeDishStoreError, "safe_url": safe_url,
            "SUPABASE_SERVICE_ROLE_KEY": "SERVER_SECRET_MUST_NOT_APPEAR",
            "OPENAI_API_KEY": "AI_SECRET_MUST_NOT_APPEAR",
        }
        tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8-sig"))
        wanted = {"admin_required", "dish_information_admin", "approve_dish_background"}
        definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
        self.assertEqual({node.name for node in definitions}, wanted)
        # Only these actual function bodies/decorators execute; no imports, app startup or DB access.
        extracted = ast.Module(body=definitions, type_ignores=[])
        exec(compile(ast.fix_missing_locations(extracted), str(ROOT / "app.py"), "exec"), namespace)
        self.client = self.app.test_client()

    def csrf(self, token="valid-review-token"):
        with self.client.session_transaction() as state:
            state["dish_information_csrf"] = token
        return token

    def approval_form(self, **updates):
        form = {
            "csrf_token": self.csrf(), "confirm_sources": "yes",
            "review_note": "Checked the linked source and the dish identity.",
            "updated_at": "2026-11-17T01:02:03+00:00",
        }
        form.update(updates)
        return form

    def assert_no_secret(self, response):
        self.assertNotIn(b"SERVER_SECRET_MUST_NOT_APPEAR", response.data)
        self.assertNotIn(b"AI_SECRET_MUST_NOT_APPEAR", response.data)

    def test_guests_and_non_admins_cannot_read_review_drafts(self):
        for user in (None, {"id": "ordinary-user", "is_admin": False}):
            with self.subTest(user=user):
                self.user = user
                response = self.client.get(REVIEW_URL)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(urlsplit(response.location).path, "/admin/login")
                self.assert_no_secret(response)
        self.get_store.assert_not_called()
        self.store.list.assert_not_called()

    def test_guests_and_non_admins_cannot_approve(self):
        form = self.approval_form()
        for user in (None, {"id": "ordinary-user", "is_admin": False}):
            with self.subTest(user=user):
                self.user = user
                response = self.client.post(APPROVE_URL, data=form)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(urlsplit(response.location).path, "/admin/login")
                self.assert_no_secret(response)
        self.get_store.assert_not_called()
        self.store.approve_background.assert_not_called()

    def test_admin_review_get_sets_and_reuses_a_session_csrf_token(self):
        response = self.client.get(REVIEW_URL)
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["template"], "admin/dish_information.html")
        self.assertEqual(payload["state"], "pending")
        self.assertTrue(payload["configured"])
        self.assertGreaterEqual(len(payload["csrf_token"]), 32)
        with self.client.session_transaction() as state:
            self.assertEqual(state["dish_information_csrf"], payload["csrf_token"])
        second = self.client.get(REVIEW_URL).get_json()
        self.assertEqual(second["csrf_token"], payload["csrf_token"])
        self.assert_no_secret(response)

    def test_review_get_restricts_state_filters_and_sanitizes_evidence_urls(self):
        self.store.list.return_value[0]["sources"].extend([
            {"title": "Unsafe", "url": "javascript:alert(1)"}, "not-a-source-record",
        ])
        response = self.client.get(REVIEW_URL + "?state=unapproved-user-supplied-filter")
        self.store.list.assert_called_once_with("dish_backgrounds", {"verification_status": "pending"})
        sources = response.get_json()["drafts"][0]["sources"]
        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[0]["url"], "https://example.org/evidence")
        self.assertFalse(sources[1]["url"])
        self.assertNotIn(b"javascript:", response.data)

    def test_missing_or_invalid_csrf_blocks_approval_before_database_access(self):
        for session_token, form_token in ((None, "anything"), ("valid-review-token", None), ("valid-review-token", "wrong")):
            with self.subTest(session_token=session_token, form_token=form_token):
                with self.client.session_transaction() as state:
                    state.clear()
                    if session_token is not None:
                        state["dish_information_csrf"] = session_token
                form = {"confirm_sources": "yes"}
                if form_token is not None:
                    form["csrf_token"] = form_token
                response = self.client.post(APPROVE_URL, data=form)
                self.assertEqual(response.status_code, 400)
                self.assert_no_secret(response)
        self.get_store.assert_not_called()
        self.store.approve_background.assert_not_called()

    def test_confirmation_of_source_review_is_required(self):
        for confirmation in (None, "no"):
            with self.subTest(confirmation=confirmation):
                form = self.approval_form()
                if confirmation is None:
                    form.pop("confirm_sources")
                else:
                    form["confirm_sources"] = confirmation
                response = self.client.post(APPROVE_URL, data=form)
                self.assertEqual(response.status_code, 400)
        self.get_store.assert_not_called()
        self.store.approve_background.assert_not_called()

    def test_valid_approval_passes_admin_identity_note_and_optimistic_lock(self):
        form = self.approval_form()
        form["reviewer_id"] = "caller-must-not-choose-the-reviewer"
        response = self.client.post(APPROVE_URL, data=form)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlsplit(response.location).path, REVIEW_URL)
        self.store.approve_background.assert_called_once_with(
            RECORD_ID, ADMIN_ID, form["review_note"], expected_updated_at=form["updated_at"],
        )
        with self.client.session_transaction() as state:
            self.assertIn(("success", "Historical and cultural information marked as reviewed."), state["_flashes"])
        self.assert_no_secret(response)

    def test_failed_store_approval_flashes_safe_error_and_redirects(self):
        self.store.approve_background.side_effect = FakeDishStoreError("This draft changed. Review the latest version.")
        response = self.client.post(APPROVE_URL, data=self.approval_form())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlsplit(response.location).path, REVIEW_URL)
        with self.client.session_transaction() as state:
            self.assertIn(("error", "This draft changed. Review the latest version."), state["_flashes"])
        self.assert_no_secret(response)

    def test_unconfigured_store_cannot_approve_and_is_reported(self):
        self.get_store.return_value = None
        response = self.client.post(APPROVE_URL, data=self.approval_form())
        self.assertEqual(response.status_code, 302)
        self.store.approve_background.assert_not_called()
        with self.client.session_transaction() as state:
            self.assertIn(("error", "Supabase is not configured."), state["_flashes"])
        self.assert_no_secret(response)

    def test_review_get_failure_renders_empty_review_list_with_a_message(self):
        self.store.list.side_effect = FakeDishStoreError("safe failure")
        response = self.client.get(REVIEW_URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["drafts"], [])
        with self.client.session_transaction() as state:
            self.assertIn(("error", "Dish information is temporarily unavailable."), state["_flashes"])
        self.assert_no_secret(response)


if __name__ == "__main__":
    unittest.main()
