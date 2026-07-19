from flask_wtf import FlaskForm
from wtforms import PasswordField, BooleanField, SelectField, StringField
from wtforms.validators import DataRequired, Length, Optional, Email

from beetsplug.beetstreamnext.schemas import USER_ROLES_SCHEMA, BITRATE_CHOICES_STR
from beetsplug.beetstreamnext.utils.text import safe_str


class LoginForm(FlaskForm):
    """
    Basic login form.
    """
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])


class UserForm(FlaskForm):
    """
    Form for creating a new user.
    """
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=64)])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=4)])
    email = StringField('Email', validators=[Optional(), Length(max=254), Email(message='Invalid email address.')])
    maxBitRate = SelectField('Max bitrate', choices=BITRATE_CHOICES_STR, coerce=int)


class EditUserForm(FlaskForm):
    """
    Form for editing an existing user.
    """
    password = PasswordField('New password (leave blank to keep current)', validators=[Optional(), Length(min=4)])
    email = StringField('Email', validators=[Optional(), Length(max=254), Email(message='Invalid email address.')])
    maxBitRate = SelectField('Max bitrate', choices=BITRATE_CHOICES_STR, coerce=int)


# Attach the role checkboxes from the registry
# (WTForms rebuilds the unbound-field list on class attribute assignment, so this is safe)
for _name, _label, _default in USER_ROLES_SCHEMA:
    setattr(UserForm, _name, BooleanField(_label, default=_default))
    setattr(EditUserForm, _name, BooleanField(_label))


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