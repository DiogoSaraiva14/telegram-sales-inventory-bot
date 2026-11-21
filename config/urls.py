from django.contrib import admin
from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from core.views import stripe_webhook

urlpatterns = [
    path('admin/', admin.site.urls),
    path('webhook/stripe/', stripe_webhook, name='stripe_webhook'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)