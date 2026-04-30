"""FOSS stub for ee.models.conversation.Conversation.

Upstream: Conversation persists Max AI assistant chats. FOSS: no AI, but the
class must resolve as a Django model so migrations referencing `to="ee.conversation"`
work and OSS code that does `Conversation.objects.filter(...)` returns empty.
"""

from django.db import models


class Conversation(models.Model):
    title = models.CharField(max_length=200, null=True)

    class Meta:
        app_label = "ee"
        db_table = "ee_conversation"
