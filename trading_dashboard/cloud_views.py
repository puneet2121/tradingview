from django.http import JsonResponse
from django.views.decorators.http import require_safe


@require_safe
def health(request):
    request.get_host()
    return JsonResponse({"status": "ok"})
