import getpass

from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.schemas import USER_ROLES_SCHEMA
from beetsplug.beetstreamnext.constants import MIN_PASSWORD_LEN
from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.console import print_box, TermColors
from beetsplug.beetstreamnext.core.maintenance import clear_caches
from beetsplug.beetstreamnext.core.users_crud import (
    create_user, delete_user, load_all_users, load_user_roles, update_user
)


def cmd_create_user(force_admin: bool = False) -> None:
    """
    CLI command: Create a new user
    """
    username_ok = False

    while not username_ok:
        username = input('Username: ')
        username_cleaned = safe_str(username)

        if username_cleaned != username:
            invalid_chars = {c for c in username if c not in username_cleaned}
            message = 'invalid characters' if len(invalid_chars) > 1 else 'an invalid character'
            chars_print = "'" + "".join(invalid_chars) + "'"
            username_ok = input(f"Username starts or ends with {message}: {chars_print}\n"
                                 f"Use '{username_cleaned}' instead? [y/n]: ").lower() == 'y'
        else:
            username_ok = True

    password_ok = False

    pw_hint = 'Password: '
    while not password_ok:
        password = getpass.getpass(pw_hint)
        if len(password) < MIN_PASSWORD_LEN:
            pw_hint = f'Password (at least {MIN_PASSWORD_LEN} chars): '
        else:
            password_ok = True

    is_admin = True if force_admin else input('Admin? [y/n]: ').lower() == 'y'

    try:
        api_key = create_user(username, password, admin=is_admin)
    except ValueError as e:
        print(f'\n[ERROR] {e}')
        return

    print_box([
        '',
        f"{TermColors.OKGREEN + TermColors.BOLD}User '{username_cleaned}' created successfully.{TermColors.ENDC}",
        '',
        f'USER API KEY: {api_key}',
        '',
        '  ▶  Enter this key in your Subsonic client instead of a password.',
        "  ▶  It won't be shown again. Store it safely.",
        '',
    ])


def cmd_update_user(username: str) -> None:
    """
    CLI command: Update an existing user's roles
    """

    current_data = load_user_roles(username)
    if not current_data:
        print(f"User '{username}' not found.")
        return

    print(f'Updating roles for user: {username}')
    print('(Press Enter to keep current value)')

    updates = {}
    for role_name, label, _ in USER_ROLES_SCHEMA:
        curr_status = 'Enabled' if current_data.get(role_name) else 'Disabled'
        val = input(f'{label} (currently {curr_status}) [y/n]: ').lower()
        if val == 'y':
            updates[role_name] = True
        elif val == 'n':
            updates[role_name] = False

    if updates:
        try:
            update_user(username, **updates)
            print(f"Successfully updated roles for '{username}'.")
        except ValueError as e:
            print(f'Error: {e}')
    else:
        print('No roles changed.')


def cmd_delete_user(username: str) -> None:
    """
    CLI command: Delete a user
    """

    confirm = input(f"Are you sure you want to delete '{username}'? [y/N]: ")
    if confirm.lower() == 'y':
        if delete_user(username):
            print(f"User '{username}' deleted.")
        else:
            print('User not found.')


def cmd_list_users() -> None:
    """
    CLI command: List all users
    """

    all_users = load_all_users()
    header = f"{'Username':<15} | {'Admin':<12} | {'Can stream':<12} | {'Can download':<12}"
    print(header)
    print('-' * len(header))

    for u in all_users:
        print(
            f"{u['username']:<15} |"
            f" {bool(u['adminRole']):<12} |"
            f" {bool(u['streamRole']):<12} |"
            f" {bool(u['downloadRole']):<12}"
        )


def cmd_change_passwd(username: str) -> None:
    """
    CLI command: Change a user's password
    """

    password_ok = False
    pw_hint = f"New password for '{username}': "

    while not password_ok:
        new_pw = getpass.getpass(pw_hint)

        if len(new_pw) < MIN_PASSWORD_LEN:
            pw_hint = f"New password for '{username}' (at least {MIN_PASSWORD_LEN} chars): "
        else:
            password_ok = True
    try:
        update_user(username, password=new_pw)
        print('Password updated successfully.')

    except ValueError as e:
        print(f'Error: {e}')


def cmd_clear_cache() -> None:
    """
    CLI command: Clear the cache
    """

    try:
        cleared = clear_caches(
            app.config['THUMBNAIL_CACHE_PATH'],
            app.config['HTTP_CACHE_PATH']
        )
        if cleared:
            print(f"Cleared: {', '.join(cleared)}.")
        else:
            print('Nothing to clear.')

    except RuntimeError as e:
        print(str(e))
