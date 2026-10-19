"""Add the definition payload fields to Workflow and Task.

Phase 2 makes a stored definition loadable losslessly, so the rows now carry
`task_type`, `depends_on`, `config` and `description` rather than just `key`.

`task_type` is non-nullable and a one-off default is used to populate any
existing rows. No interface in v0.2.0 could create a `Task` row (Phase 1 shipped
the schema with no endpoints), so in practice there is nothing to backfill; the
default exists so the migration cannot fail on a database where some other path
inserted rows. A row left with an empty `task_type` is refused by
`load_definition` with a message naming this migration, rather than being
silently loaded as a definition that cannot run.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="workflow",
            name="description",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="task",
            name="task_type",
            field=models.CharField(default="", max_length=200),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="task",
            name="depends_on",
            field=models.JSONField(default=list),
        ),
        migrations.AddField(
            model_name="task",
            name="config",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
