"""Local script storage. Pine execution stays in a restricted browser worker."""

import json
from pathlib import Path
import re

from django.db import IntegrityError, transaction
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_http_methods

from .models import PineIndicator

MAX_SOURCE_LENGTH = 50000
STATIC = Path(__file__).parent / "static"


def serialize(script):
    return {
        "id": script.id, "name": script.name, "source": script.source,
        "overlay": script.overlay, "revision": script.revision,
        "updated_at": script.updated_at.isoformat(),
    }


@require_http_methods(["GET", "POST", "PUT", "DELETE"])
@csrf_protect
def scripts(request, script_id=None):
    if request.method == "GET":
        if script_id is not None:
            script = PineIndicator.objects.filter(pk=script_id).first()
            return JsonResponse({"script": serialize(script)} if script else {"error": "Script no longer exists."}, status=200 if script else 404)
        return JsonResponse({"scripts": [serialize(script) for script in PineIndicator.objects.all()]})
    try:
        if len(request.body) > MAX_SOURCE_LENGTH * 6 + 2000:
            raise ValueError("Script request is too large.")
        payload = json.loads(request.body)
        if not isinstance(payload, dict):
            raise ValueError("Expected a JSON object.")
        with transaction.atomic():
            script = None
            if script_id is not None:
                script = PineIndicator.objects.select_for_update().get(pk=script_id)
                if payload.get("revision") != script.revision:
                    return JsonResponse({"error": "This script changed in another tab. Reopen it before saving or deleting."}, status=409)
            elif request.method != "POST":
                raise ValueError("Select a saved script first.")
            if request.method == "DELETE":
                script.delete()
                return JsonResponse({"deleted": script_id})
            if request.method == "POST" and script is not None:
                raise ValueError("Use PUT to update a script.")
            name = payload.get("name")
            source = payload.get("source")
            overlay = payload.get("overlay")
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
                raise ValueError("Name must contain 1-80 characters.")
            if not isinstance(source, str) or not 1 <= len(source) <= MAX_SOURCE_LENGTH:
                raise ValueError("Pine source must contain 1-50,000 characters.")
            if not re.match(r"\s*//@version=[56](?:\r?\n|$)", source):
                raise ValueError("Start with //@version=5 or //@version=6.")
            if not isinstance(overlay, bool):
                raise ValueError("Validate the script before saving.")
            if script is None:
                if PineIndicator.objects.count() >= 100:
                    raise ValueError("The local library is limited to 100 scripts.")
                script = PineIndicator()
            else:
                script.revision += 1
            script.name, script.source, script.overlay = name.strip(), source, overlay
            script.save()
            return JsonResponse({"script": serialize(script)}, status=201 if request.method == "POST" else 200)
    except PineIndicator.DoesNotExist:
        return JsonResponse({"error": "Script no longer exists."}, status=404)
    except IntegrityError:
        return JsonResponse({"error": "A script with this name already exists."}, status=409)
    except (ValueError, UnicodeDecodeError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@require_GET
def worker(request):
    # Bundle the runtime into the worker response so its CSP can deny ALL imports
    # and connections while allowing PineTS's local compilation via new Function.
    files = [STATIC / "vendor/pinets/pinets-0.10.0.min.js", STATIC / "dashboard/pine-worker.js"]
    response = HttpResponse("\n;\n".join(path.read_text() for path in files), content_type="application/javascript")
    response["Content-Security-Policy"] = "default-src 'none'; script-src 'unsafe-eval'; connect-src 'none'; worker-src 'none'; child-src 'none'"
    response["Cache-Control"] = "no-cache"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_GET
def trader_template(request):
    source = (Path(__file__).parent.parent / "strategies/impulsive_trader.pine").read_text()
    return JsonResponse({"source": source})


@require_GET
def scalper_template(request):
    source = (Path(__file__).parent.parent / "strategies/trend_breakdown_scalper.pine").read_text()
    return JsonResponse({"source": source})
