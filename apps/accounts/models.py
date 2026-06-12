import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    phone_number = models.CharField(max_length=32, blank=True)
    is_platform_admin = models.BooleanField(default=False)

    def __str__(self):
        return self.get_full_name() or self.email or self.username

# Create your models here.
