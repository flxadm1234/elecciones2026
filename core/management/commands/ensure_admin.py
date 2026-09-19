from django.core.management.base import BaseCommand
from django.conf import settings
from django.contrib.auth import get_user_model


User = get_user_model()


class Command(BaseCommand):
    help = "Crea el usuario administrador inicial a partir de variables .env si no existe"

    def handle(self, *args, **options):
        username = getattr(settings, "ADMIN_USERNAME", "admin")
        email = getattr(settings, "ADMIN_EMAIL", "admin@actas.local")
        password = getattr(settings, "ADMIN_PASSWORD", "Admin12345")
        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "email": email,
                "is_active": True,
                "is_staff": True,
                "is_superuser": True,
            },
        )
        if created:
            user.set_password(password)
            user.save()
            self.stdout.write(self.style.SUCCESS(f"Admin creado: {username} / {password}"))
        else:
            changed = False
            if not user.is_superuser:
                user.is_staff = True
                user.is_superuser = True
                changed = True
            if not user.check_password(password):
                user.set_password(password)
                changed = True
            if changed:
                user.save()
                self.stdout.write(self.style.SUCCESS(f"Admin actualizado: {username}"))
            else:
                self.stdout.write(f"Admin ya existe: {username}")
