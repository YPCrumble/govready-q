from django.db import models, transaction
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from django.conf import settings

from jsonfield import JSONField

from siteapp.models import User, ProjectMembership

class Discussion(models.Model):
    organization = models.ForeignKey('siteapp.Organization', related_name="discussions", help_text="The Organization that this Discussion belongs to.")

    attached_to_content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    attached_to_object_id = models.PositiveIntegerField()
    attached_to = GenericForeignKey('attached_to_content_type', 'attached_to_object_id')

    guests = models.ManyToManyField(User, blank=True, help_text="Additional Users who are participating in this chat, besides those that are members of the Project that contains the Discussion.")

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    updated = models.DateTimeField(auto_now=True, db_index=True)
    extra = JSONField(blank=True, help_text="Additional information stored with this object.")

    class Meta:
      unique_together = (('attached_to_content_type', 'attached_to_object_id'))

    @staticmethod
    def get_for(org, object, create=False, must_exist=False):
        content_type = ContentType.objects.get_for_model(object)
        if create:
            return Discussion.objects.get_or_create(organization=org, attached_to_content_type=content_type, attached_to_object_id=object.id)[0]
        elif not must_exist:
            return Discussion.objects.filter(organization=org, attached_to_content_type=content_type, attached_to_object_id=object.id).first()
        else:
            return Discussion.objects.get(organization=org, attached_to_content_type=content_type, attached_to_object_id=object.id)

    def __str__(self):
        # for the admin, notification strings
        return self.title

    def get_absolute_url(self):
        return self.attached_to.get_absolute_url() + "#discussion"

    @property
    def title(self):
        if self.attached_to is not None:
            return self.attached_to.title
        else:
            # Dangling - because it's a generic relation, there is no
            # delete protection and attached_to can get reset to None.
            return "<Deleted Discussion>"

    def is_participant(self, user):
        # No one is a participant of a dicussion attached to (a question
        # of) a deleted Task.
        if self.attached_to.task.deleted_at:
            return False

        # Participants are members of the project team of the task of
        # the question this Discussion is attached to, plus the Discussion's
        # guests.
        return user in self.get_all_participants()

    def get_all_participants(self):
        return User.objects.filter(projectmembership__project=self.attached_to.project) \
            | self.guests.all()

    def can_invite_guests(self, user):
        return ProjectMembership.objects.filter(project=self.attached_to.project, user=user).exists()

    def get_invitation_verb_inf(self, invitation):
        return "to join the discussion"

    def get_invitation_verb_past(self, invitation):
        return "joined the discussion"

    def is_invitation_valid(self, invitation):
        # Invitation remains valid only if the user that sent it is still
        # able to invite guests.
        return self.can_invite_guests(invitation.from_user)

    def accept_invitation(self, invitation, add_message):
        if self.is_participant(invitation.accepted_user):
            # user is already a participant --- possibly because they were just invited
            # and now added into the project, which gives them access to the discussion
            # --- so just redirect to it.
            return
        else:
            # add the user to the guests list for the discussion. 
            self.guests.add(invitation.accepted_user)
            add_message('You are now a participant in the discussion on %s.' % self.title)

    def get_invitation_redirect_url(self, invitation):
        return self.attached_to.get_absolute_url()

    @property
    def supress_link_from_notifications(self):
        # Dangling - because it's a generic relation, there is no
        # delete protection and attached_to can get reset to None.
        return self.attached_to is None

    def get_notification_watchers(self):
        if self.attached_to.task.deleted_at:
            return []
        return \
            list(mbr.user for mbr in ProjectMembership.objects.filter(project=self.attached_to.project)) \
            + list(self.guests.all())

    def get_autocompletes(self, user):
        # When typing in a comment, what autocompletes are available to this user?
        # Ensure the user is a participant of the discussion.
        if not self.is_participant(user):
            return []

        return {
            # @-mention other participants in the discussion
            "@": [
                {
                    "user_id": user.id,
                    "tag": user.username,
                    "display": user.render_context_dict(self.organization)["name"],
                }
                for user in self.get_all_participants()
            ]
        }


class Comment(models.Model):
    discussion = models.ForeignKey(Discussion, related_name="comments", help_text="The Discussion that this comment is attached to.")
    replies_to = models.ForeignKey('self', blank=True, null=True, related_name="replies", help_text="If this is a reply to a Comment, the Comment that this is in reply to.")
    user = models.ForeignKey(User, help_text="The user making a comment.")

    emojis = models.CharField(max_length=256, blank=True, null=True, help_text="A comma-separated list of emoji names that the user is reacting with.")
    text = models.TextField(blank=True, help_text="The text of the user's comment.")
    proposed_answer = JSONField(blank=True, null=True, help_text="A proposed answer to the question that this discussion is about.")
    deleted = models.BooleanField(default=False, help_text="Set to true if the comment has been 'deleted'.")

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    updated = models.DateTimeField(auto_now=True, db_index=True)
    extra = JSONField(blank=True, help_text="Additional information stored with this object.")

    class Meta:
        index_together = [
            ('discussion', 'user'),
        ]

    def can_see(self, user):
        if self.deleted:
            return False
        return self.discussion.is_participant(user)

    def can_edit(self, user):
        # If the comment has been deleted, it becomes locked for editing. This
        # shouldn't have a user-visible effect, since no one can see it anyway.
        if self.deleted:
            return False

        # Is the user permitted to edit this comment? If a user is no longer
        # a participant in a discussion, they can't edit their comments in that
        # discussion.
        return self.user == user and self.discussion.is_participant(user)

    def push_history(self, field):
        if not isinstance(self.extra, dict):
            self.extra = { }
        self.extra.setdefault("history", []).append({
            "when": timezone.now().isoformat(), # make JSON-serializable
            "previous-" + field: getattr(self, field),
        })

    def render_context_dict(self, whose_asking):
        if self.deleted:
            raise ValueError()

        # Render the comment text into HTML.
        # * Replace @-mentions with something.
        # * Render to HTML as if CommonMark.
        import CommonMark
        from .views import match_autocompletes
        rendered_text = self.text
        rendered_text, _ = match_autocompletes(self.discussion, rendered_text, whose_asking,
            lambda text : "**" + text + "**")
        rendered_text = CommonMark.commonmark(rendered_text)


        def get_user_role():
            if self.user == self.discussion.attached_to.task.editor:
                return "editor"
            if ProjectMembership.objects.filter(
                project=self.discussion.attached_to.project,
                user=self.user,
                is_admin=True):
                return "team admin"
            if self.user in self.discussion.guests.all():
                return "guest"
            if ProjectMembership.objects.filter(
                project=self.discussion.attached_to.project,
                user=self.user):
                return "project member"
            return "former participant"

        return {
            "type": "comment",
            "id": self.id,
            "replies_to": self.replies_to_id,
            "user": self.user.render_context_dict(self.discussion.organization),
            "user_role": get_user_role(),
            "date_relative": reldate(self.created, timezone.now()) + " ago",
            "date_posix": self.created.timestamp(), # POSIX time, seconds since the epoch, in UTC
            "text": self.text,
            "text_rendered": rendered_text,
            "notification_text": str(self.user) + ": " + self.text,
            "emojis": self.emojis.split(",") if self.emojis else None,
        }

def reldate(date, ref):
    import dateutil.relativedelta
    rd = dateutil.relativedelta.relativedelta(ref, date)
    def r(n, unit):
        return str(n) + " " + unit + ("s" if n != 1 else "")
    def c(*rs):
        return ", ".join(r(*s) for s in rs)
    if rd.months >= 1: return c((rd.months, "month"), (rd.days, "day"))
    if rd.days >= 7: return c((rd.days, "day"),)
    if rd.days >= 1: return c((rd.days, "day"), (rd.hours, "hour"))
    if rd.hours >= 1: return c((rd.hours, "hour"), (rd.minutes, "minute"))
    return c((rd.minutes, "minute"),)
