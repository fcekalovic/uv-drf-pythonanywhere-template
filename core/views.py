"""Core views, including the service health check and deploy trigger."""

import hmac
import io
import subprocess

from django.conf import settings
from django.core.management import call_command
from django.db import connection
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

# Timeout (seconds) bounding the git call that resolves the running commit for
# the health check. A stall resolves to "unknown" rather than hanging the probe.
GIT_HASH_TIMEOUT = 5


def resolve_commit_hash():
    """Return the git commit hash of the deployed checkout, or ``"unknown"``.

    Runs ``git -C <BASE_DIR> rev-parse HEAD`` as an argument list (no shell) so
    no caller-influenced input reaches the command. Any failure — non-zero
    exit, timeout, missing ``git`` or ``.git`` directory, or other exception —
    degrades to ``"unknown"`` so health-check status never depends on the hash
    being resolvable.
    """
    try:
        completed = subprocess.run(
            ["git", "-C", str(settings.BASE_DIR), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=GIT_HASH_TIMEOUT,
            check=False,
        )
    except Exception:
        return "unknown"
    if completed.returncode != 0:
        return "unknown"
    return completed.stdout.strip() or "unknown"


class HealthCheckView(APIView):
    """Unauthenticated health probe for the hosting platform.

    Reports whether the service is up and its database dependency is
    reachable. Returns HTTP 200 when healthy and HTTP 503 when the database
    cannot be reached, so a platform health probe can act on the status.

    Always responds with JSON (no browsable HTML UI), so a browser visit and
    an automated probe see the same machine-readable payload.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    renderer_classes = [JSONRenderer]

    def perform_content_negotiation(self, request, force=False):
        # Always render JSON, ignoring the client's Accept header, so a browser
        # (Accept: text/html) receives the same JSON payload as an automated
        # probe instead of an HTTP 406 Not Acceptable response.
        renderer = self.renderer_classes[0]()
        return (renderer, renderer.media_type)

    def get(self, request):
        database_ok = True
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except Exception:
            database_ok = False

        status_code = 200 if database_ok else 503
        return Response(
            {
                "status": "ok" if database_ok else "unhealthy",
                "database": "ok" if database_ok else "unreachable",
                "commit": resolve_commit_hash(),
            },
            status=status_code,
        )


# Timeout (seconds) bounding each external deploy subprocess so a hung command
# cannot block the worker indefinitely.
DEPLOY_STEP_TIMEOUT = 120


class DeployView(APIView):
    """Authenticated, self-service deploy trigger (``POST /deploy/``).

    When enabled, executes a FIXED deploy sequence against the project
    checkout — ``git pull --ff-only`` followed by ``migrate --noinput`` — and
    returns a per-step JSON report. The endpoint accepts no command, branch,
    or argument from the request: the request can only say "go".

    Security posture:

    - Disabled by default: when ``settings.DEPLOY_TOKEN`` is empty the endpoint
      responds with HTTP 404, so the deploy surface does not exist until an
      operator deliberately sets the token.
    - Authentication is a shared secret in the ``X-Deploy-Token`` header,
      compared in constant time against ``settings.DEPLOY_TOKEN``.
    - The running web app is NOT reloaded here (a worker cannot cleanly restart
      itself mid-request); reload is performed by the external caller via the
      hosting platform's API after this response is received.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    renderer_classes = [JSONRenderer]

    def perform_content_negotiation(self, request, force=False):
        # Always render JSON, ignoring the client's Accept header.
        renderer = self.renderer_classes[0]()
        return (renderer, renderer.media_type)

    def _enabled(self):
        return bool(settings.DEPLOY_TOKEN)

    def _authorized(self, request):
        provided = request.headers.get("X-Deploy-Token", "")
        # Constant-time comparison so token validity cannot be inferred from
        # response timing. compare_digest requires equal-type operands.
        return hmac.compare_digest(provided, settings.DEPLOY_TOKEN)

    def post(self, request):
        if not self._enabled():
            # Indistinguishable from "not deployed" until enabled.
            return Response(status=404)

        if not self._authorized(request):
            return Response({"detail": "Invalid or missing deploy token."}, status=401)

        steps = []
        overall_ok = True

        # Step 1: fast-forward-only pull of the tracked branch. Running git with
        # -C against BASE_DIR (rather than relying on the process CWD) and as an
        # argument list (no shell) keeps the invocation free of any
        # caller-influenced input.
        git_step = self._run_subprocess(
            "git-pull",
            ["git", "-C", str(settings.BASE_DIR), "pull", "--ff-only"],
        )
        steps.append(git_step)
        if git_step["returncode"] != 0:
            overall_ok = False

        # Step 2: apply migrations in-process (only if the pull succeeded).
        if overall_ok:
            migrate_step = self._run_migrate()
            steps.append(migrate_step)
            if migrate_step["returncode"] != 0:
                overall_ok = False

        return Response(
            {"ok": overall_ok, "steps": steps},
            status=200 if overall_ok else 500,
        )

    def _run_subprocess(self, name, argv):
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=DEPLOY_STEP_TIMEOUT,
                check=False,
            )
            output = (completed.stdout or "") + (completed.stderr or "")
            return {
                "step": name,
                "returncode": completed.returncode,
                "output": output.strip(),
            }
        except subprocess.TimeoutExpired:
            return {
                "step": name,
                "returncode": -1,
                "output": f"{name} timed out after {DEPLOY_STEP_TIMEOUT}s",
            }
        except Exception as exc:  # pragma: no cover - defensive
            return {"step": name, "returncode": -1, "output": str(exc)}

    def _run_migrate(self):
        buffer = io.StringIO()
        try:
            call_command("migrate", "--noinput", stdout=buffer, stderr=buffer)
            return {
                "step": "migrate",
                "returncode": 0,
                "output": buffer.getvalue().strip(),
            }
        except Exception as exc:
            detail = buffer.getvalue().strip()
            message = f"{detail}\n{exc}".strip() if detail else str(exc)
            return {"step": "migrate", "returncode": 1, "output": message}
