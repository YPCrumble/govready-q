# Generated by Django 2.2.4 on 2019-11-12 02:09

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='ControlService',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text='The name of this endpoint/host Control Service .', max_length=255, unique=True)),
                ('api_user', models.CharField(blank=True, help_text="The user/login identify for accessing Control Service's API.", max_length=255, null=True)),
                ('api_pw', models.CharField(blank=True, help_text="The user/login password or API KEY for accessing Control Service's API.", max_length=255, null=True)),
                ('api_root_path', models.CharField(blank=True, help_text="The domain and initial path for accessing Control Service's API.", max_length=255, null=True)),
                ('created', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated', models.DateTimeField(auto_now=True, db_index=True)),
            ],
        ),
    ]
