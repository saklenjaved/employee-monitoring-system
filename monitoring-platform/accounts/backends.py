"""Sign-in when the same email can have a separate account in each organization.

Each organization's account has its own password and status. A sign-in is matched by email and password:
if one account fits, it is used. If the same email and password fit accounts in more than one organization,
the sign-in form asks which organization to use (see accounts.forms.OrganizationLoginForm). The backend never
picks one of them by itself.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


def accounts_matching(username, password):
    """The accounts with this email whose password is `password`, ignoring whether they may sign in.

    One password check is made even when there is no account, so the answer takes about the same time.
    """
    UserModel = get_user_model()
    candidates = list(UserModel._default_manager.filter(**{UserModel.USERNAME_FIELD: username}).select_related("organization"))
    if not candidates:
        UserModel().set_password(password)  # same cost as a real check, so unknown emails are not detectable by timing
        return []
    return [user for user in candidates if user.check_password(password)]


class OrganizationBackend(ModelBackend):
    """Like Django's ModelBackend, but safe when an email has accounts in several organizations."""

    def authenticate(self, request, username=None, password=None, organization=None, **kwargs):
        if username is None:
            username = kwargs.get(get_user_model().USERNAME_FIELD)
        if username is None or password is None:
            return None
        accounts = [user for user in accounts_matching(username, password) if self.user_can_authenticate(user)]
        if organization:
            accounts = [user for user in accounts if str(user.organization_id) == str(organization)]
        # Several matches and no organization chosen: return nobody rather than guess.
        return accounts[0] if len(accounts) == 1 else None
