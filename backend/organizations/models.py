from django.db import models


class Organization(models.Model):
    """A customer company (tenant). Every organization-owned record points here."""

    name = models.CharField(max_length=200, unique=True)
    is_active = models.BooleanField(default=True)
    # Only a hash of the enrollment token is stored. IT puts the token in the installer; PCs send it when they connect.
    enrollment_token_hash = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
