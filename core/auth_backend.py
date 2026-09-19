import logging
import bcrypt
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from .models_legacy import UsuarioLegacy

log = logging.getLogger("core.auth")
User = get_user_model()


class LegacyUserBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None
        try:
            legacy = UsuarioLegacy.objects.get(usuario=username)
        except UsuarioLegacy.DoesNotExist:
            log.info("login legacy usuario no existe: %s", username)
            return None
        except Exception as exc:
            log.exception("error leyendo legacy usuario %s: %s", username, exc)
            return None

        if not legacy.estado:
            log.warning("login legacy usuario inactivo: %s", username)
            return None
        if legacy.rol not in ("SUPER_ADMIN", "ADMINISTRADOR"):
            log.warning(
                "login legacy usuario %s rechazado por rol %s",
                username,
                legacy.rol,
            )
            return None

        hashed = legacy.clave or ""
        if not hashed:
            log.warning("login legacy sin clave: %s", username)
            return None

        try:
            ok = bcrypt.checkpw(
                password.encode("utf-8"), hashed.encode("utf-8")
            )
        except ValueError as exc:
            log.warning("bcrypt inválido para %s: %s", username, exc)
            return None

        if not ok:
            log.info("login legacy clave no coincide: %s", username)
            return None

        try:
            user, _created = User.objects.get_or_create(
                username=legacy.usuario,
                defaults={
                    "is_staff": True,
                    "is_superuser": legacy.rol == "SUPER_ADMIN",
                    "is_active": True,
                    "email": f"{legacy.usuario}@legacy.local",
                },
            )
        except IntegrityError:
            try:
                user = User.objects.get(username=legacy.usuario)
            except User.DoesNotExist:
                log.exception("No se pudo obtener/crear usuario espejo %s", username)
                return None

        changed = False
        if user.is_staff is not True:
            user.is_staff = True
            changed = True
        if legacy.rol == "SUPER_ADMIN" and not user.is_superuser:
            user.is_superuser = True
            changed = True
        if not user.is_active:
            user.is_active = True
            changed = True
        if changed:
            user.save(update_fields=["is_staff", "is_superuser", "is_active"])

        return user

    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
