"""Tests for the deploy trigger endpoint."""

from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse


class HealthCheckEndpointTests(TestCase):
    url = reverse("health-check")

    def test_reports_commit_hash_when_resolvable(self):
        """A healthy response carries the resolved git commit hash."""
        with mock.patch("core.views.resolve_commit_hash", return_value="abc123def456"):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["database"], "ok")
        self.assertEqual(body["commit"], "abc123def456")

    def test_unresolvable_commit_hash_still_returns_200(self):
        """When the git hash cannot be resolved, the probe stays healthy."""
        git_failure = mock.Mock(returncode=1, stdout="", stderr="fatal: not a repo")
        with mock.patch("core.views.subprocess.run", return_value=git_failure):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["database"], "ok")
        self.assertEqual(body["commit"], "unknown")


class DeployEndpointTests(TestCase):
    url = reverse("deploy")

    @override_settings(DEPLOY_TOKEN="")
    def test_returns_404_when_token_unset(self):
        """Disabled by default: no DEPLOY_TOKEN -> the endpoint does not exist."""
        with mock.patch("core.views.subprocess.run") as run:
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 404)
        run.assert_not_called()

    @override_settings(DEPLOY_TOKEN="s3cret-token")
    def test_returns_401_on_missing_token(self):
        with mock.patch("core.views.subprocess.run") as run:
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 401)
        run.assert_not_called()
        # The expected token is never echoed back.
        self.assertNotContains(response, "s3cret-token", status_code=401)

    @override_settings(DEPLOY_TOKEN="s3cret-token")
    def test_returns_401_on_wrong_token(self):
        with mock.patch("core.views.subprocess.run") as run:
            response = self.client.post(self.url, headers={"X-Deploy-Token": "nope"})
        self.assertEqual(response.status_code, 401)
        run.assert_not_called()

    @override_settings(DEPLOY_TOKEN="s3cret-token")
    def test_get_not_allowed(self):
        """Only POST triggers a deploy; GET is rejected with 405."""
        with mock.patch("core.views.subprocess.run") as run:
            response = self.client.get(
                self.url, headers={"X-Deploy-Token": "s3cret-token"}
            )
        self.assertEqual(response.status_code, 405)
        run.assert_not_called()

    @override_settings(DEPLOY_TOKEN="s3cret-token")
    def test_successful_deploy_reports_each_step(self):
        git_result = mock.Mock(returncode=0, stdout="Already up to date.", stderr="")
        with (
            mock.patch("core.views.subprocess.run", return_value=git_result) as run,
            mock.patch("core.views.call_command") as call_command,
        ):
            response = self.client.post(
                self.url, headers={"X-Deploy-Token": "s3cret-token"}
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        step_names = [s["step"] for s in body["steps"]]
        self.assertEqual(step_names, ["git-pull", "migrate"])
        run.assert_called_once()
        call_command.assert_called_once()

    @override_settings(DEPLOY_TOKEN="s3cret-token")
    def test_failed_git_step_skips_migrate_and_returns_500(self):
        git_result = mock.Mock(returncode=1, stdout="", stderr="fatal: not a ff")
        with (
            mock.patch("core.views.subprocess.run", return_value=git_result),
            mock.patch("core.views.call_command") as call_command,
        ):
            response = self.client.post(
                self.url, headers={"X-Deploy-Token": "s3cret-token"}
            )
        self.assertEqual(response.status_code, 500)
        body = response.json()
        self.assertFalse(body["ok"])
        step_names = [s["step"] for s in body["steps"]]
        # migrate must NOT run after git-pull fails.
        self.assertEqual(step_names, ["git-pull"])
        call_command.assert_not_called()

    @override_settings(DEPLOY_TOKEN="s3cret-token")
    def test_failed_migrate_step_returns_500(self):
        git_result = mock.Mock(returncode=0, stdout="Updated.", stderr="")
        with (
            mock.patch("core.views.subprocess.run", return_value=git_result),
            mock.patch(
                "core.views.call_command", side_effect=Exception("migration boom")
            ),
        ):
            response = self.client.post(
                self.url, headers={"X-Deploy-Token": "s3cret-token"}
            )
        self.assertEqual(response.status_code, 500)
        body = response.json()
        self.assertFalse(body["ok"])
        migrate_step = next(s for s in body["steps"] if s["step"] == "migrate")
        self.assertNotEqual(migrate_step["returncode"], 0)
        self.assertIn("migration boom", migrate_step["output"])

    @override_settings(DEPLOY_TOKEN="s3cret-token")
    def test_request_body_does_not_influence_commands(self):
        """A caller-supplied branch/command in the body is ignored."""
        git_result = mock.Mock(returncode=0, stdout="ok", stderr="")
        with (
            mock.patch("core.views.subprocess.run", return_value=git_result) as run,
            mock.patch("core.views.call_command"),
        ):
            response = self.client.post(
                self.url,
                data={"branch": "evil", "command": "rm -rf /"},
                content_type="application/json",
                headers={"X-Deploy-Token": "s3cret-token"},
            )
        self.assertEqual(response.status_code, 200)
        # The git invocation is the fixed argument list, with no caller input.
        argv = run.call_args.args[0]
        self.assertEqual(argv[:2], ["git", "-C"])
        self.assertEqual(argv[-2:], ["pull", "--ff-only"])
        self.assertNotIn("evil", argv)
        self.assertNotIn("rm -rf /", argv)
