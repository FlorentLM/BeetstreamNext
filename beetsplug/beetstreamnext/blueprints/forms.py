from __future__ import annotations

import flask
from flask_wtf import FlaskForm
from wtforms import PasswordField, BooleanField, SelectField, StringField
from wtforms.validators import DataRequired, EqualTo, Length, Optional, Email

from beetsplug.beetstreamnext.core.accounts.user_schema import USER_ROLES_SCHEMA
from beetsplug.beetstreamnext.constants import BITRATE_CHOICES_STR
from beetsplug.beetstreamnext.constants import MIN_PASSWORD_LEN
from beetsplug.beetstreamnext.utils.text import safe_str


def new_password_field(label: str = 'New password', *, required: bool = True) -> PasswordField:
    """New password field with the minimum-length check."""
    presence = DataRequired() if required else Optional()
    return PasswordField(label, validators=[presence, Length(min=MIN_PASSWORD_LEN)])


class PasswordConfirmMixin:
    """Adds a `confirm_password` field that must match `password`."""
    confirm_password = PasswordField(
        'Confirm password',
        validators=[DataRequired(), EqualTo('password', message='Passwords must match.')]
    )


class LoginForm(FlaskForm):
    """
    Basic login form.
    """
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])


class OnboardingForm(PasswordConfirmMixin, FlaskForm):
    """
    Create the first admin account.
    """
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=64)])
    password = new_password_field('Password')
    setup_key = PasswordField('Server key', validators=[DataRequired()])


class UserForm(FlaskForm):
    """
    Form for creating a new user.
    """
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=64)])
    password = new_password_field('Password')
    email = StringField('Email', validators=[Optional(), Length(max=254), Email(message='Invalid email address.')])
    maxBitRate = SelectField('Max bitrate', choices=BITRATE_CHOICES_STR, coerce=int)


class EditUserForm(FlaskForm):
    """
    Form for editing an existing user.
    """
    password = new_password_field('New password (leave blank to keep current)', required=False)
    email = StringField('Email', validators=[Optional(), Length(max=254), Email(message='Invalid email address.')])
    maxBitRate = SelectField('Max bitrate', choices=BITRATE_CHOICES_STR, coerce=int)


class AccountProfileForm(FlaskForm):
    """
    Form for a user editing their own settings. Choices for maxBitRate are set per request.
    """
    email = StringField('Email', validators=[Optional(), Length(max=254), Email(message='Invalid email address.')])
    maxBitRate = SelectField('Max bitrate', choices=BITRATE_CHOICES_STR, coerce=int)


class ChangePasswordForm(PasswordConfirmMixin, FlaskForm):
    """
    Form for a user changing their own password.
    """
    current_password = PasswordField('Current password', validators=[DataRequired()])
    password = new_password_field()


class DeleteAccountForm(FlaskForm):
    """
    Form for a user deleting their own account: requires the current password.
    """
    current_password = PasswordField('Current password', validators=[DataRequired()])


class RadioStationForm(FlaskForm):
    """
    Form for creating/editing a radio station.
    """
    name = StringField('Name', validators=[DataRequired(), Length(max=128)])
    streamUrl = StringField('Stream URL', validators=[DataRequired(), Length(max=1024)])
    homepageUrl = StringField('Homepage URL', validators=[Optional(), Length(max=1024)])


class ArtistImageForm(FlaskForm):
    """
    Form for uploading a manual artist image.
    """
    name = StringField('Artist', validators=[DataRequired(), Length(max=256)])


# Attach the role checkboxes from the registry
# (WTForms rebuilds the unbound-field list on class attribute assignment, so this is safe)
for _name, _label, _default in USER_ROLES_SCHEMA:
    setattr(UserForm, _name, BooleanField(_label, default=_default))
    setattr(EditUserForm, _name, BooleanField(_label))


def form_error_messages(form: FlaskForm) -> list[str]:
    """Every WTForms validation error on form, as display strings."""
    return [
        error if field_name == 'confirm_password' else f'{field_name}: {error}'
        for field_name, errors in form.errors.items()
        for error in errors
    ]


def flash_form_errors(form: FlaskForm) -> None:
    """Flash every WTForms validation error on form (one flash message per error)."""
    for msg in form_error_messages(form):
        flask.flash(msg, 'error')


def collect_form_data(form: FlaskForm) -> dict:
    """
    Extract data from the form. Functions in `users_crud` do the filtering of non-db fields.
    """
    data = form.data.copy()

    if data.get('email'):
        data['email'] = safe_str(data['email'])

    # Remove password if it's an 'Edit' form and the field is empty
    if 'password' in data and not data['password']:
        data.pop('password')

    return data