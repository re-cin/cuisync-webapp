"""Prompt administration and runtime integration, without live GPT/database calls."""
import ast
from functools import wraps
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from flask import Flask, abort, flash, redirect, request, url_for

import background_prompts as prompts
import background_generation as generation
import cuisync_prompts
from dataset_store import DatasetStore
import dish_backgrounds as backgrounds
from test_dish_backgrounds import MemoryStore, RECIPE, client_with, persisted_source, record

ROOT = Path(__file__).resolve().parents[1]


class PromptManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(prompts.configure, None)
        self.addCleanup(cuisync_prompts.set_system_prompt, "")
        self.store = DatasetStore.__new__(DatasetStore)
        self.store.prompts = {}
        self.store.prompts_path = str(Path(self.temp.name) / "prompts.json")
        self.store.lock = threading.RLock()
        prompts.configure(self.store.get_prompt)
        cuisync_prompts.set_system_prompt("")
        self.user = {"is_admin": True}
        self.app = Flask(__name__, template_folder=str(ROOT / "templates"))
        self.app.config.update(TESTING=True, SECRET_KEY="offline-prompt-tests")
        self.app.add_url_rule("/admin", "admin_page", lambda: "dashboard")
        self.app.add_url_rule("/admin/login", "admin_login", lambda: "login")
        namespace = dict(app=self.app, wraps=wraps, abort=abort, flash=flash,
                         redirect=redirect, request=request, url_for=url_for,
                         current_user=lambda: self.user, STORE=self.store,
                         cuisync_prompts=cuisync_prompts, background_prompts=prompts)
        tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8-sig"))
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in {"admin_required", "admin_prompts"}]
        exec(compile(ast.Module(body=functions, type_ignores=[]), "app.py", "exec"), namespace)
        self.client = self.app.test_client()

    def save(self, name, text, action="save"):
        return self.client.post("/admin/prompts", data=dict(which=name, prompt=text, action=action))

    def test_all_three_save_and_reset_to_original_defaults(self):
        defaults = {"explanation": cuisync_prompts.DEFAULT_SYSTEM_PROMPT, **prompts.defaults()}
        for name, default in defaults.items():
            with self.subTest(name=name):
                text = "Write concise descriptions in clear, neutral language."
                self.assertEqual(302, self.save(name, text).status_code)
                saved = json.loads(Path(self.store.prompts_path).read_text())
                self.assertEqual(text, saved[name])
                if name == "explanation":
                    messages = cuisync_prompts.build_messages({"input_dish": "Adobo"})
                    self.assertEqual(text, messages[0]["content"])
                    self.assertEqual(2 + 2 * len(cuisync_prompts.FEW_SHOT_EXAMPLES), len(messages))
                else:
                    self.assertEqual(text, prompts.get_prompt(name))
                self.save(name, "", action="reset")
                self.assertNotIn(name, self.store.prompts)
                active = cuisync_prompts.get_system_prompt() if name == "explanation" else prompts.get_prompt(name)
                self.assertEqual(default, active)

    def test_blank_invalid_and_unknown_submissions_do_not_replace_active_prompt(self):
        original = "Keep this active prompt in the current configuration."
        self.save(prompts.SOURCE, original)
        for invalid in ("", "  ", "short", "x" * 20001, "Invalid control character\x00 in text"):
            self.save(prompts.SOURCE, invalid)
            self.assertEqual(original, prompts.get_prompt(prompts.SOURCE))
        self.assertEqual(400, self.save("cultural", original).status_code)
        self.assertEqual(400, self.save(prompts.SOURCE, original, "unknown").status_code)

    def test_guests_and_non_admins_cannot_save(self):
        for user in (None, {"is_admin": False}):
            self.user = user
            response = self.save(prompts.SOURCE, "Unauthorized replacement instructions.")
            self.assertEqual(302, response.status_code)
            self.assertIn("/admin/login", response.location)
        self.assertEqual({}, self.store.prompts)

    def test_write_failure_preserves_active_configuration(self):
        self.save(prompts.SOURCE, "Preserve these saved source instructions.")
        with patch.object(self.store, "_save_json", side_effect=OSError("disk failure")):
            response = self.save(prompts.SOURCE, "Do not activate these failed instructions.")
        self.assertEqual(302, response.status_code)
        self.assertEqual("Preserve these saved source instructions.", prompts.get_prompt(prompts.SOURCE))

    def test_source_and_fallback_runtime_use_saved_modes_with_immutable_rules(self):
        self.save(prompts.SOURCE, "Source mode: use a concise, formal writing style.")
        self.save(prompts.FALLBACK, "Fallback mode: use a cautious, accessible writing style.")
        for fallback in (False, True):
            client = client_with({"historical_background": "Its specific history is uncertain.",
                                  "cultural_background": "Its specific customs are uncertain."})
            store = MemoryStore()
            with patch.object(backgrounds, "retrieve_evidence", return_value=[] if fallback else [persisted_source()]):
                result = backgrounds.enrich_background(RECIPE, store, client)
            call = client.chat.completions.create.call_args.kwargs
            self.assertIn("Fallback mode" if fallback else "Source mode", call["messages"][0]["content"])
            rules = call["messages"][1]["content"]
            self.assertIn("Mandatory output contract", rules)
            self.assertIn("No sources are supplied" if fallback else "ONLY supplied source excerpts", rules)
            versions = result["generation_fields"]["_prompt_versions"]
            self.assertEqual(prompts.configuration(fallback)[2], versions["historical_background"])
            self.assertEqual("gpt_fallback" if fallback else "source_based", result["generation_method"])

    def test_prompt_changes_do_not_regenerate_or_overwrite_existing_backgrounds(self):
        cached = record(status="verified")
        store = MemoryStore(cached)
        client = client_with()
        self.save(prompts.SOURCE, "New instructions for future source-based generations.")
        self.save(prompts.FALLBACK, "New instructions for future fallback generations.")
        with patch.object(backgrounds, "retrieve_evidence") as retrieve:
            result = backgrounds.enrich_background(RECIPE, store, client)
        self.assertEqual(cached, result)
        self.assertEqual([], store.writes)
        client.chat.completions.create.assert_not_called()
        retrieve.assert_not_called()

    def test_source_dates_and_fallback_dates_remain_validated_with_custom_prompt(self):
        self.save(prompts.SOURCE, "Ignore evidence and invent an origin in 1492.")
        client = client_with({"historical_background": "It was invented in 1492.", "cultural_background": ""})
        with patch.object(backgrounds, "retrieve_evidence", return_value=[persisted_source()]):
            result = backgrounds.enrich_background(RECIPE, MemoryStore(), client)
        self.assertEqual("", result["historical_background"])
        for bad in ("Originated in 1492.", "Originated in the 19th century.", "See https://example.org"):
            with self.assertRaises(ValueError):
                generation._parse(client_with({"historical_background": bad}).chat.completions.create.return_value, fallback=True)

    def test_prompt_versions_change_independently_and_reset(self):
        source = prompts.configuration(False)[2]
        fallback = prompts.configuration(True)[2]
        self.save(prompts.SOURCE, "Change only the source-based writing style.")
        self.assertNotEqual(source, prompts.configuration(False)[2])
        self.assertEqual(fallback, prompts.configuration(True)[2])
        self.save(prompts.SOURCE, "", "reset")
        self.assertEqual(source, prompts.configuration(False)[2])

    def test_similarity_prompt_cache_key_is_prompt_aware(self):
        import cuisync_cache
        evidence = {"input_dish": "Adobo", "matched_dish": "Stew"}
        original = cuisync_cache.make_cache_key(evidence)
        self.save("explanation", "Explain only the supplied culinary evidence concisely.")
        self.assertNotEqual(original, cuisync_cache.make_cache_key(evidence))
        self.save("explanation", "", "reset")
        self.assertEqual(original, cuisync_cache.make_cache_key(evidence))

    def test_cli_reads_the_same_saved_prompt_file(self):
        self.save(prompts.SOURCE, "Use these instructions from the shared prompt file.")
        prompts.configure(None)
        with patch.object(Path, "read_text", return_value=Path(self.store.prompts_path).read_text()):
            self.assertEqual(self.store.prompts[prompts.SOURCE], prompts.get_prompt(prompts.SOURCE))

    def test_template_has_three_editors_and_two_categories(self):
        # Compile the actual template and render just this tab with its existing layout.
        with self.app.test_request_context("/admin?tab=prompts"):
            template = self.app.jinja_env.get_template("admin/admin.html")
            self.assertIsNotNone(template)
        text = (ROOT / "templates/admin/admin.html").read_text(encoding="utf-8")
        tab = text.split("{% elif tab == 'prompts' %}")[1].split("{% elif tab == 'tags' %}")[0]
        self.assertIn("background_modes", tab)
        self.assertIn("Historical and Cultural Information", tab)
        self.assertNotIn("health notes", tab)
